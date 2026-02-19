"""Patrol 자율 순찰 — 1시간 주기.

순찰 범위:
1. Base 전체 종합 체크 (이상 징후 감지)
2. ③ llm_evaluated 트리거 평가 (Patrol에서만)
3. Tavily 브라우징 소스 체크
4. 유저 패턴 기반 능동 서치
5. patrol_deferred 대기 요청 처리
+ Base 온도 관리 (Hot→Warm→Cold)
+ 비활성 유저 Patrol 주기 확대
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from src.config import PRO_PATROL_INTERVAL_SECONDS
from src.db.models import (
    BaseStream,
    ChatMessage,
    PatrolLog,
    User,
    UserTrigger,
)
from src.monitoring.base import BaseManager

logger = logging.getLogger(__name__)


class PatrolService:
    """자율 순찰 서비스 — APScheduler로 스케줄링."""

    # ------------------------------------------------------------------
    # 메인 순찰 루프
    # ------------------------------------------------------------------

    @staticmethod
    async def run_patrol(
        session: AsyncSession,
        user: User,
        bot: Bot,
    ) -> dict[str, Any]:
        """유저별 1시간 순찰 실행.

        Returns:
            순찰 결과 요약 dict
        """
        findings: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        temp_changes: dict[str, int] = {}

        try:
            # 1) Base 온도 관리
            temp_changes = await BaseManager.auto_transition_temperatures(
                session, user.id,
            )
            if temp_changes.get("hot_to_warm") or temp_changes.get("warm_to_cold"):
                actions.append({
                    "type": "temperature_transition",
                    "changes": temp_changes,
                })

            # 2) Base 이상 징후 감지
            anomalies = await PatrolService._check_base_anomalies(session, user)
            if anomalies:
                findings.extend(anomalies)

            # 2-b) 이상 징후 기반 자동 트리거 생성 (source="patrol")
            auto_triggers = await PatrolService._auto_generate_triggers(
                session, user, anomalies, bot,
            )
            if auto_triggers:
                actions.extend(auto_triggers)

            # 3) ③ llm_evaluated 트리거 평가
            eval_results = await PatrolService._evaluate_llm_triggers(
                session, user, bot,
            )
            if eval_results:
                findings.extend(eval_results)

            # 4) 미매매 시그널 감지 (Q4 피드백)
            try:
                from src.feedback.processor import check_unfollowed_signals

                await check_unfollowed_signals(session, user)
            except Exception:
                logger.warning("미매매 시그널 감지 실패", exc_info=True)

            # 5) patrol_deferred 대기 요청 처리
            deferred = await PatrolService._process_deferred_requests(
                session, user, bot,
            )
            if deferred:
                actions.extend(deferred)

            # 5) PatrolLog 기록
            log = PatrolLog(
                user_id=user.id,
                patrol_type="scheduled",
                findings=findings,
                actions_taken=actions,
                base_temp_changes=temp_changes if temp_changes else None,
            )
            session.add(log)
            await session.flush()

            logger.info(
                "순찰 완료: user=%s, findings=%d, actions=%d",
                user.telegram_id, len(findings), len(actions),
            )

        except Exception:
            logger.error(
                "순찰 실패: user=%s", user.telegram_id, exc_info=True,
            )

        return {
            "findings": findings,
            "actions": actions,
            "temp_changes": temp_changes,
        }

    # ------------------------------------------------------------------
    # 2) Base 이상 징후 감지
    # ------------------------------------------------------------------

    @staticmethod
    async def _check_base_anomalies(
        session: AsyncSession,
        user: User,
    ) -> list[dict[str, Any]]:
        """Hot/Warm 스트림의 이상 징후 감지.

        급등/급락, OI 급변, 펀딩비 극단값 등.
        """
        anomalies: list[dict[str, Any]] = []

        result = await session.execute(
            select(BaseStream).where(
                BaseStream.user_id == user.id,
                BaseStream.temperature.in_(["hot", "warm"]),
                BaseStream.last_value.isnot(None),
            )
        )
        streams = result.scalars().all()

        for stream in streams:
            val = stream.last_value
            if not isinstance(val, dict):
                continue

            anomaly = _detect_anomaly(stream.stream_type, stream.symbol, val)
            if anomaly:
                anomalies.append(anomaly)

        return anomalies

    # ------------------------------------------------------------------
    # 3) ③ llm_evaluated 트리거 평가
    # ------------------------------------------------------------------

    @staticmethod
    async def _evaluate_llm_triggers(
        session: AsyncSession,
        user: User,
        bot: Bot,
    ) -> list[dict[str, Any]]:
        """수치 정의 불가 트리거를 LLM으로 평가."""
        result = await session.execute(
            select(UserTrigger).where(
                UserTrigger.user_id == user.id,
                UserTrigger.trigger_type == "llm_evaluated",
                UserTrigger.is_active.is_(True),
            )
        )
        triggers = result.scalars().all()
        if not triggers:
            return []

        findings: list[dict[str, Any]] = []

        for trigger in triggers:
            try:
                met = await _llm_evaluate_trigger(session, user, trigger)
                findings.append({
                    "trigger_id": trigger.id,
                    "description": trigger.description,
                    "condition_met": met,
                })

                if met:
                    # 조건 충족 → 유저에게 알림
                    text = (
                        f"🧠 순찰 결과: {trigger.description}\n"
                        f"조건이 충족된 것으로 판단돼."
                    )
                    trigger.triggered_at = datetime.now(timezone.utc)
                    trigger.is_active = False

                    session.add(ChatMessage(
                        user_id=user.id,
                        role="assistant",
                        content=text,
                        message_type="text",
                        intent="patrol_deferred",
                    ))
                    await session.flush()

                    try:
                        await bot.send_message(
                            chat_id=user.telegram_id, text=text,
                        )
                    except Exception:
                        logger.error("순찰 알림 전송 실패", exc_info=True)

            except Exception:
                logger.error(
                    "LLM 트리거 평가 실패: trigger=%d", trigger.id, exc_info=True,
                )

        return findings

    # ------------------------------------------------------------------
    # 5) patrol_deferred 대기 요청 처리
    # ------------------------------------------------------------------

    @staticmethod
    async def _process_deferred_requests(
        session: AsyncSession,
        user: User,
        bot: Bot,
    ) -> list[dict[str, Any]]:
        """채팅에서 patrol_deferred로 분류된 요청 처리."""
        result = await session.execute(
            select(UserTrigger).where(
                UserTrigger.user_id == user.id,
                UserTrigger.trigger_type == "llm_evaluated",
                UserTrigger.source == "user_request",
                UserTrigger.is_active.is_(True),
                UserTrigger.triggered_at.is_(None),  # 아직 미평가
            )
        )
        deferred_triggers = result.scalars().all()

        actions: list[dict[str, Any]] = []
        for trigger in deferred_triggers:
            try:
                met = await _llm_evaluate_trigger(session, user, trigger)
                actions.append({
                    "type": "deferred_evaluated",
                    "trigger_id": trigger.id,
                    "description": trigger.description,
                    "result": met,
                })

                # 결과 전송
                if met:
                    text = f"📋 대기 요청 결과: {trigger.description}\n확인 결과, 조건 충족이야."
                    trigger.triggered_at = datetime.now(timezone.utc)
                    trigger.is_active = False
                else:
                    text = (
                        f"📋 대기 요청 체크: {trigger.description}\n"
                        f"아직 조건 미충족. 다음 순찰에서 다시 확인할게."
                    )
                    trigger.triggered_at = datetime.now(timezone.utc)

                session.add(ChatMessage(
                    user_id=user.id,
                    role="assistant",
                    content=text,
                    message_type="text",
                    intent="patrol_deferred",
                ))
                await session.flush()

                try:
                    await bot.send_message(chat_id=user.telegram_id, text=text)
                except Exception:
                    logger.error("대기 요청 결과 전송 실패", exc_info=True)

            except Exception:
                logger.error(
                    "대기 요청 처리 실패: trigger=%d", trigger.id, exc_info=True,
                )

        return actions

    # ------------------------------------------------------------------
    # 2-b) Patrol 자동 트리거 생성 (source="patrol")
    # ------------------------------------------------------------------

    @staticmethod
    async def _auto_generate_triggers(
        session: AsyncSession,
        user: User,
        anomalies: list[dict[str, Any]],
        bot: Bot,
    ) -> list[dict[str, Any]]:
        """이상 징후 + 유저 패턴 기반 자동 트리거 생성.

        - 유저 주력 종목에서 이상 징후 발견 시 알림 트리거 자동 생성
        - 이미 동일 조건 트리거가 있으면 스킵 (중복 방지)
        """
        if not anomalies:
            return []

        from src.intelligence.pattern import analyze_patterns

        actions: list[dict[str, Any]] = []

        # 유저 주력 종목 파악 (top_symbols: [("BTC/USDT", 15), ...])
        patterns = await analyze_patterns(session, user.id)
        primary_symbols: set[str] = set()
        if patterns and patterns.get("top_symbols"):
            for sym_name, _count in patterns["top_symbols"]:
                # "BTC/USDT" → "BTC", "ETH/KRW" → "ETH"
                base = sym_name.split("/")[0] if "/" in sym_name else sym_name
                primary_symbols.add(base)

        # 유저의 활성 트리거 조회 (중복 방지)
        existing_result = await session.execute(
            select(UserTrigger).where(
                UserTrigger.user_id == user.id,
                UserTrigger.is_active.is_(True),
                UserTrigger.source == "patrol",
            )
        )
        existing_descs = {
            t.description for t in existing_result.scalars().all()
        }

        for anomaly in anomalies:
            symbol = anomaly.get("symbol")
            if not symbol:
                continue

            # 유저 관심 종목이 아니면 스킵
            if primary_symbols and symbol not in primary_symbols:
                continue

            desc = anomaly.get("detail", f"{symbol} 이상 징후")
            if desc in existing_descs:
                continue

            # 이상 징후를 유저에게 알림 + 관련 트리거 생성
            severity = anomaly.get("severity", "medium")
            a_type = anomaly.get("type", "unknown")

            # 알림 메시지 전송
            emoji = "🚨" if severity == "high" else "⚡"
            text = f"{emoji} 순찰 감지: {desc}\n네 관심 종목이라 알려줘."

            session.add(ChatMessage(
                user_id=user.id,
                role="assistant",
                content=text,
                message_type="text",
                intent="patrol_deferred",
            ))

            # 후속 추적용 LLM 평가 트리거 생성
            ut = UserTrigger(
                user_id=user.id,
                trigger_type="llm_evaluated",
                eval_prompt=f"{desc} — 이 상황이 매매 기회인지 위험인지 평가",
                data_needed=["news", "sentiment"],
                description=desc,
                source="patrol",
            )
            session.add(ut)

            try:
                await bot.send_message(chat_id=user.telegram_id, text=text)
            except Exception:
                logger.error("Patrol 알림 전송 실패", exc_info=True)

            actions.append({
                "type": "auto_trigger_created",
                "anomaly": a_type,
                "symbol": symbol,
                "description": desc,
            })
            logger.info(
                "Patrol 자동 트리거: user=%s, %s",
                user.telegram_id, desc,
            )

        if actions:
            await session.flush()

        return actions

    # ------------------------------------------------------------------
    # 비활성 유저 감지
    # ------------------------------------------------------------------

    @staticmethod
    def should_skip_patrol(user: User) -> bool:
        """비활성 유저(24시간+) Patrol 스킵 여부.

        완전 스킵이 아니라, 2회 중 1회만 실행 (주기 2배).
        """
        if not user.last_active_at:
            return False
        inactive_hours = (
            datetime.now(timezone.utc) - user.last_active_at
        ).total_seconds() / 3600
        # 24시간 이상 비활성 → 짝수 시간에만 실행
        if inactive_hours > 24:
            current_hour = datetime.now(timezone.utc).hour
            return current_hour % 2 != 0
        return False


# ------------------------------------------------------------------
# LLM 기반 트리거 평가 (③)
# ------------------------------------------------------------------


async def _llm_evaluate_trigger(
    session: AsyncSession,
    user: User,
    trigger: UserTrigger,
) -> bool:
    """LLM으로 트리거 조건 충족 여부 평가.

    data_needed 기반으로 데이터 수집 → eval_prompt + 데이터로 LLM 질의.
    """
    from src.data.search import autonomous_search
    from src.llm.client import llm_client

    eval_prompt = trigger.eval_prompt or trigger.description
    data_needed = trigger.data_needed or []

    # 데이터 수집: Base + 서치
    context_parts: list[str] = []

    # Base 데이터
    result = await session.execute(
        select(BaseStream).where(
            BaseStream.user_id == user.id,
            BaseStream.temperature.in_(["hot", "warm"]),
            BaseStream.last_value.isnot(None),
        )
    )
    streams = result.scalars().all()
    if streams:
        base_lines = []
        for s in streams:
            base_lines.append(f"- {s.stream_type}/{s.symbol}: {s.last_value}")
        context_parts.append("## Base 데이터\n" + "\n".join(base_lines[:20]))

    # Tavily 서치 (news/social 등 필요시)
    search_needed = any(
        d in data_needed
        for d in ["news", "social", "sentiment", "general"]
    )
    if search_needed:
        try:
            search_results = await autonomous_search(
                query=eval_prompt,
                user_language=user.language or "ko",
            )
            if search_results and search_results != "검색 결과 없음":
                context_parts.append(f"## 검색 결과\n{search_results[:2000]}")
        except Exception:
            logger.warning("순찰 서치 실패", exc_info=True)

    context = "\n\n".join(context_parts) if context_parts else "수집 데이터 없음"

    # LLM 평가
    system_prompt = (
        "너는 시장 조건 평가 시스템이야. "
        "아래 조건이 현재 충족되었는지 판단해. "
        "반드시 첫 줄에 'YES' 또는 'NO'만 출력하고, "
        "그 다음 줄에 간단한 근거를 1~2문장으로 작성해."
    )
    user_message = (
        f"## 평가할 조건\n{eval_prompt}\n\n"
        f"## 현재 데이터\n{context}"
    )

    try:
        resp = await llm_client.patrol(
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=200,
        )
        answer = resp.text.strip()
        return answer.upper().startswith("YES")
    except Exception:
        logger.error("LLM 트리거 평가 호출 실패", exc_info=True)
        return False


# ------------------------------------------------------------------
# 이상 징후 감지 로직
# ------------------------------------------------------------------


def _detect_anomaly(
    stream_type: str,
    symbol: str | None,
    value: dict[str, Any],
) -> dict[str, Any] | None:
    """Base 데이터에서 이상 징후 감지.

    Returns:
        {"type": "anomaly_type", "symbol": ..., "detail": ...} or None
    """
    if stream_type == "price":
        # 24시간 변화율 ±10% 이상
        change = value.get("change_24h_pct")
        if change is not None:
            try:
                change = float(change)
                if abs(change) >= 10:
                    direction = "급등" if change > 0 else "급락"
                    return {
                        "type": f"price_{direction}",
                        "symbol": symbol,
                        "detail": f"{symbol} 24h {change:+.1f}%",
                        "severity": "high" if abs(change) >= 20 else "medium",
                    }
            except (ValueError, TypeError):
                pass

    elif stream_type == "funding":
        rate = value.get("rate")
        if rate is not None:
            try:
                rate = float(rate)
                if abs(rate) >= 0.05:  # ±5% 펀딩비 극단값
                    return {
                        "type": "funding_extreme",
                        "symbol": symbol,
                        "detail": f"{symbol} 펀딩비 {rate*100:.2f}%",
                        "severity": "high",
                    }
            except (ValueError, TypeError):
                pass

    elif stream_type == "oi":
        change = value.get("change_pct")
        if change is not None:
            try:
                change = float(change)
                if abs(change) >= 15:  # OI ±15% 급변
                    return {
                        "type": "oi_surge",
                        "symbol": symbol,
                        "detail": f"{symbol} OI {change:+.1f}%",
                        "severity": "medium",
                    }
            except (ValueError, TypeError):
                pass

    return None


# ------------------------------------------------------------------
# APScheduler 설정 헬퍼
# ------------------------------------------------------------------


async def patrol_job(bot: Bot) -> None:
    """APScheduler에서 호출하는 순찰 작업."""
    from src.db.models import User
    from src.db.session import async_session_factory

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(User).where(
                    User.onboarding_step >= 4,
                    User.is_active.is_(True),
                )
            )
            users = result.scalars().all()

            for user in users:
                if PatrolService.should_skip_patrol(user):
                    logger.debug("비활성 유저 순찰 스킵: %s", user.telegram_id)
                    continue
                await PatrolService.run_patrol(session, user, bot)

    except Exception:
        logger.error("Patrol 작업 실패", exc_info=True)
