"""Feedback 순환 학습 — 시그널 피드백 + 매매 결과 자동 피드백.

Q4 파이프라인:
  시그널/브리핑 → 유저 피드백 (동의/반대/세부조정) → Intelligence 패턴 업데이트
  시그널 → 매매 → 결과 → Signal + Trade + Episode 자동 연결 → 패턴 강화/교정
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Episode, Signal, Trade, User
from src.intelligence.episode import create_episode

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 시그널 피드백 처리
# ------------------------------------------------------------------


async def process_signal_feedback(
    session: AsyncSession,
    user: User,
    signal_id: int,
    user_feedback: str | None = None,
    agreed: bool | None = None,
) -> None:
    """시그널/브리핑 피드백 처리 → Intelligence 순환.

    Args:
        session: DB 세션
        user: 유저 객체
        signal_id: 피드백 대상 시그널 ID
        user_feedback: 자연어 피드백 텍스트 (있으면)
        agreed: True=동의, False=반대, None=미응답

    처리 흐름:
    1. Signal DB 업데이트 (user_feedback, user_agreed)
    2. 에피소드 생성 (type="feedback")
    3. Intelligence 패턴 업데이트:
       - 동의 → 유사 상황 confidence 강화
       - 반대 → 유저가 다르게 보는 관점 학습
       - 세부조정 → 조건 세부 튜닝
    """
    result = await session.execute(
        select(Signal).where(Signal.id == signal_id)
    )
    signal = result.scalar_one_or_none()
    if not signal:
        logger.warning("피드백 대상 시그널 없음: signal_id=%d", signal_id)
        return

    # 1) Signal DB 업데이트
    if agreed is not None:
        signal.user_agreed = agreed
    if user_feedback:
        signal.user_feedback = user_feedback

    await session.flush()

    # 2) 에피소드 생성
    feedback_type = _classify_feedback(agreed, user_feedback)
    embedding_parts = [
        f"시그널 피드백 ({feedback_type}): {signal.symbol or 'unknown'}",
        f"시그널: {signal.content[:200]}",
    ]
    if user_feedback:
        embedding_parts.append(f"유저 의견: {user_feedback[:200]}")

    episode = await create_episode(
        session,
        user,
        episode_type="feedback",
        user_action=f"시그널 피드백 ({feedback_type}): {signal.symbol or ''}",
        embedding_text=" | ".join(embedding_parts),
        reasoning=user_feedback or f"{'동의' if agreed else '반대'}",
        trade_data={
            "signal_id": signal.id,
            "signal_type": signal.signal_type,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "confidence": signal.confidence,
            "user_agreed": agreed,
        },
    )

    # 시그널에 에피소드 연결
    signal.episode_id = episode.id
    await session.flush()

    logger.info(
        "시그널 피드백 처리: user=%s, signal=%d, type=%s",
        user.telegram_id, signal_id, feedback_type,
    )


# ------------------------------------------------------------------
# 매매 결과 자동 피드백 (Q4)
# ------------------------------------------------------------------


async def process_trade_result_feedback(
    session: AsyncSession,
    user: User,
    trade: Trade,
) -> None:
    """매매 청산 시 관련 시그널 자동 연결 + Intelligence 업데이트.

    시그널 → 매매 → 결과 감지 시 자동 호출.
    trade_detector.handle_trade_close()에서 호출됨.

    처리:
    1. 해당 매매와 관련된 최근 시그널 찾기
    2. Signal DB 업데이트 (trade_followed, trade_result_pnl)
    3. 에피소드 생성 (시그널+매매+결과 연결)
    4. Intelligence 패턴 업데이트:
       - 시그널 → 매매 → 수익: 패턴 강화
       - 시그널 → 매매 → 손실: 패턴 교정
       - 시그널 → 미매매: 유저가 다른 판단 (학습)
    """
    if not trade.pnl_percent:
        return

    # 1) 관련 시그널 찾기 (같은 종목, 매매 시점 ±24시간)
    signal = await _find_related_signal(session, user.id, trade)
    if not signal:
        logger.debug(
            "관련 시그널 없음: trade=%d, %s", trade.id, trade.symbol,
        )
        return

    # 2) Signal 업데이트
    signal.trade_followed = True
    signal.trade_result_pnl = trade.pnl_percent
    await session.flush()

    # 3) 결과 분류
    is_profit = trade.pnl_percent > 0
    signal_direction = signal.direction or "watch"
    trade_direction = trade.side

    # 시그널 방향과 매매 방향이 일치하는지
    direction_match = _directions_match(signal_direction, trade_direction)

    # 4) 에피소드 생성 (시그널+매매+결과 연결)
    result_label = "적중" if (direction_match and is_profit) else "미스"
    if not direction_match:
        result_label = "반대매매"

    embedding_text = (
        f"시그널 결과 ({result_label}): {trade.symbol} "
        f"시그널={signal_direction} 매매={trade_direction} "
        f"결과={trade.pnl_percent:+.1f}% "
        f"근거: {signal.reasoning[:200] if signal.reasoning else '없음'}"
    )

    await create_episode(
        session,
        user,
        episode_type="feedback",
        user_action=f"매매 결과 피드백 ({result_label}): {trade.symbol} {trade.pnl_percent:+.1f}%",
        embedding_text=embedding_text,
        trade_data={
            "signal_id": signal.id,
            "trade_id": trade.id,
            "symbol": trade.symbol,
            "signal_direction": signal_direction,
            "trade_direction": trade_direction,
            "pnl_percent": trade.pnl_percent,
            "result": result_label,
            "confidence": signal.confidence,
        },
        trade_result={
            "pnl_percent": trade.pnl_percent,
            "direction_match": direction_match,
            "result": result_label,
        },
        reasoning=f"시그널 {signal_direction} → 매매 {trade_direction} → {trade.pnl_percent:+.1f}%",
        auto_collect_market=True,
    )

    logger.info(
        "매매 결과 피드백: user=%s, trade=%d, signal=%d, result=%s (%.1f%%)",
        user.telegram_id, trade.id, signal.id, result_label, trade.pnl_percent,
    )


# ------------------------------------------------------------------
# 미매매 시그널 자동 감지
# ------------------------------------------------------------------


async def check_unfollowed_signals(
    session: AsyncSession,
    user: User,
) -> None:
    """시그널 발송 후 24시간 내 매매 없으면 '미매매' 기록.

    Patrol에서 호출.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    result = await session.execute(
        select(Signal).where(
            Signal.user_id == user.id,
            Signal.signal_type == "trade_signal",
            Signal.trade_followed.is_(None),
            Signal.created_at < cutoff,
        )
    )
    unfollowed = result.scalars().all()

    for signal in unfollowed:
        signal.trade_followed = False

        await create_episode(
            session,
            user,
            episode_type="feedback",
            user_action=f"시그널 미매매: {signal.symbol or ''} {signal.direction or ''}",
            embedding_text=(
                f"시그널 미매매: {signal.symbol} {signal.direction} "
                f"conf={signal.confidence:.0%} — 유저가 따르지 않음"
            ),
            trade_data={
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "confidence": signal.confidence,
                "result": "unfollowed",
            },
            reasoning="유저가 시그널을 따르지 않음 — 다른 판단을 한 것으로 학습",
        )

    if unfollowed:
        await session.flush()
        logger.info(
            "미매매 시그널 감지: user=%s, %d건",
            user.telegram_id, len(unfollowed),
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _classify_feedback(
    agreed: bool | None,
    user_feedback: str | None,
) -> str:
    """피드백 유형 분류."""
    if agreed is True:
        if user_feedback:
            return "동의+세부"
        return "동의"
    elif agreed is False:
        return "반대"
    return "세부조정" if user_feedback else "미응답"


async def _find_related_signal(
    session: AsyncSession,
    user_id: int,
    trade: Trade,
) -> Signal | None:
    """매매와 관련된 최근 시그널 찾기.

    같은 종목 + 매매 시점 ±24시간 내 시그널.
    """
    if not trade.opened_at:
        return None

    window_start = trade.opened_at - timedelta(hours=24)
    window_end = trade.opened_at + timedelta(hours=1)

    # 심볼 정규화 (SOL/USDT → SOL)
    trade_symbol_base = (trade.symbol or "").split("/")[0].upper()

    result = await session.execute(
        select(Signal).where(
            Signal.user_id == user_id,
            Signal.signal_type == "trade_signal",
            Signal.created_at.between(window_start, window_end),
        ).order_by(Signal.created_at.desc())
    )
    signals = result.scalars().all()

    for sig in signals:
        sig_symbol_base = (sig.symbol or "").split("/")[0].upper()
        if sig_symbol_base == trade_symbol_base:
            return sig

    return None


def _directions_match(signal_direction: str, trade_side: str) -> bool:
    """시그널 방향과 매매 방향이 일치하는지."""
    long_set = {"long", "buy"}
    short_set = {"short", "sell"}

    sig = signal_direction.lower()
    trade = trade_side.lower()

    if sig in long_set and trade in long_set:
        return True
    if sig in short_set and trade in short_set:
        return True
    return False
