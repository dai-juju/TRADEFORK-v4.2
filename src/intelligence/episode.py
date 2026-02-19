"""에피소드 CRUD + Intelligence 컨텍스트 구축.

Intelligence Module 핵심:
- create_episode(): 에피소드 생성 + 시장 컨텍스트 자동 수집 + Pinecone upsert
- build_intelligence_context(): 모든 LLM 호출에 주입되는 통합 컨텍스트
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    BaseStream,
    ChatMessage,
    Episode,
    Principle,
    Signal,
    Trade,
    User,
)
from src.intelligence.vector_store import vector_store

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 에피소드 CRUD
# ------------------------------------------------------------------


async def create_episode(
    session: AsyncSession,
    user: User,
    *,
    episode_type: str,
    user_action: str,
    embedding_text: str,
    market_context: dict[str, Any] | None = None,
    trade_data: dict[str, Any] | None = None,
    reasoning: str | None = None,
    trade_result: dict[str, Any] | None = None,
    expression_calibration: dict[str, Any] | None = None,
    style_tags: dict[str, Any] | None = None,
    auto_collect_market: bool = False,
) -> Episode:
    """에피소드 생성 + Pinecone upsert.

    Args:
        session: DB 세션
        user: 유저 객체
        episode_type: "trade" | "chat" | "feedback" | "signal" | "patrol"
        user_action: 유저 행동 요약
        embedding_text: Pinecone 임베딩용 텍스트
        market_context: 시장 상황 JSON (직접 전달)
        trade_data: 매매 데이터 JSON
        reasoning: 근거
        trade_result: 결과 JSON
        expression_calibration: 캘리브레이션 데이터
        style_tags: 스타일 태그
        auto_collect_market: True면 시장 컨텍스트 자동 수집

    Returns:
        생성된 Episode 객체
    """
    # 시장 컨텍스트 자동 수집 (trade/signal 에피소드에 유용)
    if auto_collect_market and not market_context:
        market_context = await _collect_market_context(session, user)

    # user 속성을 미리 캡처 — flush 후 session 상태 변경 시 lazy load 방지
    _user_id = user.id
    _telegram_id = user.telegram_id

    episode = Episode(
        user_id=_user_id,
        episode_type=episode_type,
        user_action=user_action,
        embedding_text=embedding_text,
        market_context=market_context,
        trade_data=trade_data,
        reasoning=reasoning,
        trade_result=trade_result,
        expression_calibration=expression_calibration,
        style_tags=style_tags,
    )
    session.add(episode)
    await session.flush()

    logger.info(
        "에피소드 생성: user=%s, type=%s, id=%s",
        _telegram_id,
        episode_type,
        episode.id,
    )

    # Pinecone upsert (실패해도 에피소드는 저장)
    try:
        pinecone_id = await vector_store.upsert_episode(
            telegram_id=_telegram_id,
            episode_id=episode.id,
            text=embedding_text,
        )
        episode.pinecone_id = pinecone_id
        await session.flush()
    except Exception:
        logger.warning(
            "Pinecone upsert/flush 실패 (episode=%s), DB만 저장됨",
            episode.id,
            exc_info=True,
        )
        # StaleDataError 등으로 session이 오염된 경우 rollback 후 계속 진행
        try:
            await session.rollback()
        except Exception:
            pass

    return episode


async def get_recent_episodes(
    session: AsyncSession,
    user_id: int,
    limit: int = 5,
) -> list[Episode]:
    """최근 에피소드 N개 조회 (최신순)."""
    result = await session.execute(
        select(Episode)
        .where(Episode.user_id == user_id)
        .order_by(Episode.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_similar_episodes(
    telegram_id: int,
    query: str,
    top_k: int = 3,
) -> list[int]:
    """Pinecone 유사 에피소드 ID 검색."""
    try:
        return await vector_store.search_similar(
            telegram_id=telegram_id,
            query=query,
            top_k=top_k,
        )
    except Exception:
        logger.warning("유사 에피소드 검색 실패", exc_info=True)
        return []


async def get_episodes_by_ids(
    session: AsyncSession,
    episode_ids: list[int],
) -> list[Episode]:
    """ID 목록으로 에피소드 조회."""
    if not episode_ids:
        return []
    result = await session.execute(
        select(Episode).where(Episode.id.in_(episode_ids))
    )
    return list(result.scalars().all())


# ------------------------------------------------------------------
# 시장 컨텍스트 자동 수집
# ------------------------------------------------------------------


async def _collect_market_context(
    session: AsyncSession,
    user: User,
) -> dict[str, Any]:
    """에피소드 생성 시 시장 상황 자동 수집.

    수집 범위:
    1. Base 데이터 (Hot + Warm)
    2. 보유 포지션 현황
    """
    context: dict[str, Any] = {}

    # 1) Hot + Warm Base 데이터
    try:
        result = await session.execute(
            select(BaseStream).where(
                BaseStream.user_id == user.id,
                BaseStream.temperature.in_(["hot", "warm"]),
            )
        )
        streams = result.scalars().all()
        base_data: list[dict[str, Any]] = []
        for s in streams:
            base_data.append({
                "type": s.stream_type,
                "symbol": s.symbol,
                "temp": s.temperature,
                "value": s.last_value,
            })
        if base_data:
            context["base_streams"] = base_data
    except Exception:
        logger.warning("Base 데이터 수집 실패", exc_info=True)

    # 2) 보유 포지션
    try:
        result = await session.execute(
            select(Trade).where(
                Trade.user_id == user.id,
                Trade.status == "open",
            )
        )
        open_trades = result.scalars().all()
        if open_trades:
            context["open_positions"] = [
                {
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_price": t.entry_price,
                    "leverage": t.leverage,
                    "exchange": t.exchange,
                }
                for t in open_trades
            ]
    except Exception:
        logger.warning("포지션 수집 실패", exc_info=True)

    return context


# ------------------------------------------------------------------
# Intelligence 컨텍스트 빌더 (중앙 집중)
# ------------------------------------------------------------------


async def build_intelligence_context(
    session: AsyncSession,
    user: User,
    current_message: str = "",
) -> dict[str, str]:
    """모든 LLM 호출에 주입할 Intelligence 컨텍스트 구축.

    구성 (CLAUDE.md Intelligence Module 참조):
      정적 (캐싱 대상 — 1~4):
        1. 유저 프로필 (스타일, 언어, 티어)
        2. 투자 원칙 (Q3) 전체
        3. 학습된 패턴 (주 종목, 전략, 수익/손실, 빈도)
        4. 표현 캘리브레이션 ({"좀 빠진다": -3%})
      동적 (매 요청 변경 — 5~8):
        5. 최근 에피소드 5개
        6. 유사 에피소드 3개 (Pinecone)
        7. 현재 보유 포지션
        8. 최근 시그널 + 피드백

    Returns:
        {
            "intelligence_context": 정적 1~4 통합 텍스트,
            "principles": 원칙 텍스트,
            "base_data": Hot 스트림 데이터,
            "positions": 보유 포지션,
            "recent_chat": 최근 대화 10개,
        }
    """
    from src.intelligence.calibration import get_calibration_context, get_style_context
    from src.intelligence.pattern import analyze_patterns, format_patterns_context

    # =========================================================
    # 정적 부분 (1~4) — Intelligence 컨텍스트로 통합
    # =========================================================
    intel_parts: list[str] = []

    # 1) 유저 프로필
    style_text = await get_style_context(session, user)
    profile_lines = [
        f"언어: {user.language or 'ko'}",
        f"티어: {user.tier or 'pro'}",
    ]
    if style_text and style_text != "스타일 정보 없음":
        profile_lines.append(style_text)
    intel_parts.append("### 프로필\n" + "\n".join(profile_lines))

    # 2) 투자 원칙 (별도 필드로도 반환)
    result = await session.execute(
        select(Principle).where(
            Principle.user_id == user.id,
            Principle.is_active.is_(True),
        )
    )
    principles_list = result.scalars().all()
    principles_text = "\n".join(
        f"{i+1}. {p.content}" for i, p in enumerate(principles_list)
    ) or "설정된 원칙 없음"

    # 3) 학습된 패턴
    patterns = await analyze_patterns(session, user.id)
    patterns_text = format_patterns_context(patterns)
    if patterns_text and patterns_text != "매매 이력 없음":
        intel_parts.append("### 매매 패턴\n" + patterns_text)

    # 4) 캘리브레이션
    cal_text = await get_calibration_context(session, user.id)
    if cal_text and cal_text != "캘리브레이션 데이터 없음":
        intel_parts.append("### 표현 캘리브레이션\n" + cal_text)

    # =========================================================
    # 동적 부분 (5~8)
    # =========================================================

    # 5) 최근 에피소드 5개
    recent_eps = await get_recent_episodes(session, user.id, limit=5)
    ep_texts: list[str] = []
    for ep in recent_eps:
        ep_texts.append(
            f"- [{ep.episode_type}] {ep.user_action} "
            f"(근거: {ep.reasoning or '없음'})"
        )

    # 6) 유사 에피소드 3개 (Pinecone)
    if current_message:
        similar_ids = await get_similar_episodes(
            telegram_id=user.telegram_id,
            query=current_message,
            top_k=3,
        )
        recent_ids = {ep.id for ep in recent_eps}
        unique_similar_ids = [eid for eid in similar_ids if eid not in recent_ids]
        if unique_similar_ids:
            similar_eps = await get_episodes_by_ids(session, unique_similar_ids)
            for ep in similar_eps:
                ep_texts.append(
                    f"- [유사/{ep.episode_type}] {ep.user_action}"
                )

    episodes_text = "\n".join(ep_texts) if ep_texts else "에피소드 없음"
    intel_parts.append("### 에피소드\n" + episodes_text)

    # 7) 현재 보유 포지션
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user.id,
            Trade.status == "open",
        )
    )
    open_trades = result.scalars().all()
    pos_lines: list[str] = []
    for t in open_trades:
        pos_lines.append(
            f"- {t.symbol} {t.side} @ {t.entry_price} (x{t.leverage})"
        )
    positions = "\n".join(pos_lines) if pos_lines else "보유 포지션 없음"

    # 8) 최근 시그널 + 피드백
    result = await session.execute(
        select(Signal)
        .where(Signal.user_id == user.id)
        .order_by(Signal.created_at.desc())
        .limit(5)
    )
    recent_signals = result.scalars().all()
    signal_lines: list[str] = []
    for sig in recent_signals:
        fb = ""
        if sig.user_feedback:
            fb = f" (피드백: {sig.user_feedback})"
        elif sig.user_agreed is not None:
            fb = f" ({'동의' if sig.user_agreed else '반대'})"
        signal_lines.append(
            f"- {sig.signal_type}: {sig.content[:100]}{fb}"
        )
    if signal_lines:
        intel_parts.append("### 최근 시그널\n" + "\n".join(signal_lines))

    # =========================================================
    # Base 데이터 (Hot 스트림)
    # =========================================================
    result = await session.execute(
        select(BaseStream).where(
            BaseStream.user_id == user.id,
            BaseStream.temperature == "hot",
        )
    )
    hot_streams = result.scalars().all()
    base_lines: list[str] = []
    for s in hot_streams:
        val = s.last_value or {}
        base_lines.append(
            f"- {s.stream_type}/{s.symbol}: {json.dumps(val, ensure_ascii=False)}"
        )
    base_data = "\n".join(base_lines) if base_lines else "실시간 데이터 없음"

    # =========================================================
    # 최근 대화 10개
    # =========================================================
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == user.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(10)
    )
    recent_msgs = list(reversed(result.scalars().all()))
    chat_lines: list[str] = []
    for m in recent_msgs:
        role_label = "유저" if m.role == "user" else "FORKER"
        content_preview = m.content[:200]
        chat_lines.append(f"{role_label}: {content_preview}")
    recent_chat = "\n".join(chat_lines) if chat_lines else "대화 기록 없음"

    # =========================================================
    # 통합 반환
    # =========================================================
    intelligence_context = "\n\n".join(intel_parts)

    return {
        "intelligence_context": intelligence_context,
        "principles": principles_text,
        "base_data": base_data,
        "positions": positions,
        "recent_chat": recent_chat,
    }
