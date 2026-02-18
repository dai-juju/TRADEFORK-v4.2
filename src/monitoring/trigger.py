"""Tier 1 User Trigger — 3단계 트리거 매칭 + 발동.

① 경량 알림 🔔: 단순 조건(price_above/below 등). Base 실시간 매칭. LLM 없음. 비용 0.
② 시그널 트리거 🎯: 복합 조건. LLM이 분해한 코드 로직. Tier 2→3 파이프라인.
③ llm_evaluated 🧠: 수치 정의 불가. Patrol에서만 LLM 평가.

evaluate_all()은 Hot 데이터 업데이트마다 호출 — ①②만 체크.
③은 Patrol에서만 처리.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from src.db.models import ChatMessage, Signal, UserTrigger

logger = logging.getLogger(__name__)


class TriggerManager:
    """3단계 트리거 매니저."""

    # ------------------------------------------------------------------
    # 전체 평가 — ①② 체크 (Hot 데이터 업데이트마다 호출)
    # ------------------------------------------------------------------

    @staticmethod
    async def evaluate_all(
        session: AsyncSession,
        user_id: int,
        current_data: dict[str, Any],
        bot: Bot,
        telegram_id: int,
    ) -> int:
        """유저의 활성 트리거 중 ①②를 현재 데이터와 매칭.

        Args:
            current_data: {"price/BTC": {"last": 100000}, ...}

        Returns:
            발동된 트리거 수
        """
        result = await session.execute(
            select(UserTrigger).where(
                UserTrigger.user_id == user_id,
                UserTrigger.is_active.is_(True),
                UserTrigger.trigger_type.in_(["alert", "signal"]),
            )
        )
        triggers = result.scalars().all()
        if not triggers:
            return 0

        fired = 0
        for trigger in triggers:
            try:
                matched = _check_condition(trigger, current_data)
                if matched:
                    if trigger.trigger_type == "alert":
                        await TriggerManager.fire_alert(
                            session, trigger, bot, telegram_id,
                        )
                    else:
                        await TriggerManager.fire_signal(
                            session, trigger, bot, telegram_id,
                        )
                    fired += 1
            except Exception:
                logger.error(
                    "트리거 평가 실패: trigger_id=%d", trigger.id, exc_info=True,
                )

        return fired

    # ------------------------------------------------------------------
    # ① 경량 알림 — 즉시 전송
    # ------------------------------------------------------------------

    @staticmethod
    async def fire_alert(
        session: AsyncSession,
        trigger: UserTrigger,
        bot: Bot,
        telegram_id: int,
    ) -> None:
        """경량 알림 🔔 즉시 전송 + 트리거 비활성화."""
        desc = trigger.description or "알림 조건 충족"
        cond = trigger.condition or {}

        symbol = cond.get("symbol", "")
        value = cond.get("value", "")

        text = f"🔔 알림: {desc}"
        if symbol and value:
            text = f"🔔 {symbol} {_condition_label(cond)} ({value})\n{desc}"

        # 비활성화 (일회성 알림)
        trigger.is_active = False
        trigger.triggered_at = datetime.now(timezone.utc)

        # DB 기록
        session.add(ChatMessage(
            user_id=trigger.user_id,
            role="assistant",
            content=text,
            message_type="text",
            intent="alert",
        ))
        await session.flush()

        try:
            await bot.send_message(chat_id=telegram_id, text=text)
        except Exception:
            logger.error("알림 전송 실패: %s", telegram_id, exc_info=True)

        logger.info("경량 알림 발동: trigger=%d, user=%s", trigger.id, telegram_id)

    # ------------------------------------------------------------------
    # ② 시그널 트리거 — Tier 2→3 파이프라인 시작
    # ------------------------------------------------------------------

    @staticmethod
    async def fire_signal(
        session: AsyncSession,
        trigger: UserTrigger,
        bot: Bot,
        telegram_id: int,
    ) -> None:
        """시그널 트리거 🎯 발동 → Tier 2 수집 → Tier 3 판단."""
        from src.db.models import User
        from src.monitoring.collector import collect_deep
        from src.monitoring.judge import judge_signal

        desc = trigger.description or "시그널 조건 충족"
        trigger.triggered_at = datetime.now(timezone.utc)

        # 유저 조회
        user_result = await session.execute(
            select(User).where(User.id == trigger.user_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            logger.error("시그널 유저 없음: user_id=%d", trigger.user_id)
            return

        # 중간 알림
        text = f"🎯 시그널 감지: {desc}\n분석 중..."
        session.add(ChatMessage(
            user_id=trigger.user_id,
            role="assistant",
            content=text,
            message_type="text",
            intent="signal_trigger",
        ))
        await session.flush()

        try:
            await bot.send_message(chat_id=telegram_id, text=text)
        except Exception:
            logger.error("시그널 알림 전송 실패: %s", telegram_id, exc_info=True)

        logger.info("시그널 트리거 발동: trigger=%d, user=%s", trigger.id, telegram_id)

        # Tier 2 심층 수집
        try:
            collected = await collect_deep(session, user, trigger)
        except Exception:
            logger.error("Tier 2 수집 실패: trigger=%d", trigger.id, exc_info=True)
            return

        # Tier 3 AI 판단 (Opus)
        try:
            await judge_signal(session, user, collected, trigger, bot)
        except Exception:
            logger.error("Tier 3 판단 실패: trigger=%d", trigger.id, exc_info=True)


# ------------------------------------------------------------------
# 조건 매칭 로직 (LLM 없음, 순수 코드)
# ------------------------------------------------------------------


def _check_condition(
    trigger: UserTrigger,
    current_data: dict[str, Any],
) -> bool:
    """트리거 조건이 현재 데이터와 매칭되는지 확인.

    Args:
        trigger: 트리거 객체
        current_data: {"price/BTC": {"last": 100000}, "funding/BTC": {"rate": 0.01}, ...}
    """
    cond = trigger.condition
    if not cond or not isinstance(cond, dict):
        # composite 트리거: composite_logic으로 처리
        if trigger.composite_logic:
            return _check_composite(trigger, current_data)
        return False

    cond_type = cond.get("type", "")
    symbol = cond.get("symbol", "")

    # news_keyword는 value 불필요 — 먼저 처리
    if cond_type == "news_keyword":
        keyword = cond.get("keyword", "")
        news_key = "news/all"
        news_data = current_data.get(news_key, {})
        headlines = news_data.get("headlines", []) if isinstance(news_data, dict) else []
        return any(keyword.lower() in h.lower() for h in headlines if isinstance(h, str))

    # 나머지 조건은 value(숫자) 필수
    raw_value = cond.get("value")
    if raw_value is None:
        return False

    try:
        value = float(raw_value)
    except (ValueError, TypeError):
        return False

    # 현재 가격 가져오기
    price_key = f"price/{symbol}"
    price_data = current_data.get(price_key, {})
    current_price = _extract_number(price_data, "last")

    if cond_type == "price_above":
        return current_price is not None and current_price >= value

    if cond_type == "price_below":
        return current_price is not None and current_price <= value

    if cond_type == "funding_above":
        fund_key = f"funding/{symbol}"
        fund_data = current_data.get(fund_key, {})
        rate = _extract_number(fund_data, "rate")
        return rate is not None and rate >= value

    if cond_type == "funding_below":
        fund_key = f"funding/{symbol}"
        fund_data = current_data.get(fund_key, {})
        rate = _extract_number(fund_data, "rate")
        return rate is not None and rate <= value

    if cond_type == "volume_spike":
        price_vol = _extract_number(price_data, "volume_ratio")
        return price_vol is not None and price_vol >= value

    if cond_type == "oi_change":
        oi_key = f"oi/{symbol}"
        oi_data = current_data.get(oi_key, {})
        change = _extract_number(oi_data, "change_pct")
        return change is not None and abs(change) >= value

    if cond_type == "kimchi_premium":
        spread_key = "spread/kimchi"
        spread_data = current_data.get(spread_key, {})
        premium = _extract_number(spread_data, "premium_pct")
        return premium is not None and premium >= value

    return False


def _check_composite(
    trigger: UserTrigger,
    current_data: dict[str, Any],
) -> bool:
    """복합 조건 매칭 (composite_logic 파싱).

    간단한 비교 연산을 안전하게 평가.
    """
    logic = trigger.composite_logic
    if not logic:
        return False

    # 안전한 변수 바인딩: current_data에서 값 추출
    # composite_logic 예: "top3_volume > btc_volume"
    # 현재는 기본적인 비교만 지원 (Phase 8에서 확장)
    try:
        # Base 데이터에서 필요한 값을 미리 추출
        streams_needed = trigger.base_streams_needed or []
        variables: dict[str, float] = {}

        for stream_info in streams_needed:
            if isinstance(stream_info, dict):
                st = stream_info.get("stream_type", "")
                sym = stream_info.get("symbol") or stream_info.get("source", "")
                key = f"{st}/{sym}"
                data = current_data.get(key, {})
                if isinstance(data, dict):
                    for k, v in data.items():
                        try:
                            variables[f"{st}_{k}"] = float(v)
                        except (ValueError, TypeError):
                            pass

        # 아직 충분한 데이터가 없으면 미매칭
        if not variables:
            return False

        # 간단한 비교: "a > b" 형태만 지원
        parts = logic.strip().split()
        if len(parts) == 3:
            left_name, op, right_name = parts
            left_val = variables.get(left_name)
            right_val = variables.get(right_name)
            if left_val is not None and right_val is not None:
                if op == ">":
                    return left_val > right_val
                if op == "<":
                    return left_val < right_val
                if op == ">=":
                    return left_val >= right_val
                if op == "<=":
                    return left_val <= right_val
                if op == "==":
                    return left_val == right_val

    except Exception:
        logger.debug("composite 조건 평가 실패: %s", logic, exc_info=True)

    return False


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _extract_number(data: Any, key: str) -> float | None:
    """dict에서 숫자 추출."""
    if not isinstance(data, dict):
        return None
    val = data.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _condition_label(cond: dict[str, Any]) -> str:
    """조건 타입을 사람 읽기 가능한 라벨로."""
    labels = {
        "price_above": "가격 도달",
        "price_below": "가격 이하",
        "funding_above": "펀딩비 이상",
        "funding_below": "펀딩비 이하",
        "volume_spike": "거래대금 급증",
        "oi_change": "OI 변화",
        "kimchi_premium": "김프 도달",
        "news_keyword": "뉴스 키워드",
    }
    return labels.get(cond.get("type", ""), "조건 충족")


def _extract_symbol_from_trigger(trigger: UserTrigger) -> str | None:
    """트리거에서 종목 심볼 추출."""
    if trigger.condition and isinstance(trigger.condition, dict):
        return trigger.condition.get("symbol")
    return None
