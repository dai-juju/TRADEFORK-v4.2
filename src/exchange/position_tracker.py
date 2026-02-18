"""보유 포지션 모니터링 — 평균 익절/손절 대비 코멘터리."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from src.db.models import ChatMessage, Principle, Trade, User
from src.exchange.manager import get_exchange, get_user_connections

logger = logging.getLogger(__name__)


async def monitor_positions(
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    """보유 중인 포지션 모니터링 — 임계점 도달 시 코멘터리."""

    # open 매매 조회
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user.id,
            Trade.status == "open",
        )
    )
    open_trades = result.scalars().all()
    if not open_trades:
        return

    # 평균 익절/손절 통계
    stats = await _get_stats(session, user.id)

    # 원칙에서 손절 기준 추출
    stop_loss_pct = await _extract_stop_loss(session, user.id)

    connections = await get_user_connections(session, user.id)
    exchange_set = {c.exchange for c in connections}

    for trade in open_trades:
        if trade.exchange not in exchange_set:
            continue
        if not trade.entry_price:
            continue

        try:
            async with get_exchange(session, user.id, trade.exchange) as ex:
                ticker = await ex.fetch_ticker(trade.symbol)
                current_price = ticker.get("last", 0)
        except Exception:
            continue

        if not current_price:
            continue

        # 현재 수익률 계산
        if trade.side in ("buy", "long"):
            pnl_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
        else:
            pnl_pct = ((trade.entry_price - current_price) / trade.entry_price) * 100

        commentary = _build_commentary(
            trade, pnl_pct, current_price, stats, stop_loss_pct,
        )

        if commentary:
            session.add(
                ChatMessage(
                    user_id=user.id,
                    role="assistant",
                    content=commentary,
                    message_type="text",
                    intent="position_commentary",
                )
            )
            await session.commit()
            try:
                await bot.send_message(chat_id=user.telegram_id, text=commentary)
            except Exception:
                logger.error("포지션 코멘터리 전송 실패: %s", user.telegram_id, exc_info=True)
            logger.info(
                "포지션 코멘터리: user=%s, %s pnl=%.1f%%",
                user.telegram_id,
                trade.symbol,
                pnl_pct,
            )


def _build_commentary(
    trade: Trade,
    pnl_pct: float,
    current_price: float,
    stats: dict[str, float],
    stop_loss_pct: float | None,
) -> str | None:
    """임계점 기반 코멘터리 생성. 알림 기준 미달 시 None."""

    avg_win = stats.get("avg_win", 0)
    avg_loss = stats.get("avg_loss", 0)

    # 평균 익절 초과
    if avg_win > 0 and pnl_pct > 0 and pnl_pct >= avg_win:
        return (
            f"📊 {trade.symbol} +{pnl_pct:.1f}% (현재가 {current_price:,.0f})\n"
            f"너 평균 익절 +{avg_win:.1f}%인데 넘었어."
        )

    # 손절 기준 도달
    if stop_loss_pct and pnl_pct < 0 and abs(pnl_pct) >= abs(stop_loss_pct):
        return (
            f"⚠️ {trade.symbol} {pnl_pct:.1f}% (현재가 {current_price:,.0f})\n"
            f"너 원칙에서 손절 {stop_loss_pct:.0f}%라고 했잖아."
        )

    # 평균 손절 초과 (손절 원칙 없는 경우)
    if avg_loss < 0 and pnl_pct < 0 and pnl_pct <= avg_loss:
        return (
            f"📊 {trade.symbol} {pnl_pct:.1f}% (현재가 {current_price:,.0f})\n"
            f"너 평균 손절 {avg_loss:.1f}%야. 한번 봐봐."
        )

    return None


async def _get_stats(
    session: AsyncSession,
    user_id: int,
) -> dict[str, float]:
    """평균 익절/손절 조회."""
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.status == "closed",
            Trade.pnl_percent.isnot(None),
        )
    )
    closed = result.scalars().all()
    if not closed:
        return {"avg_win": 12.0, "avg_loss": -5.0}  # 기본값

    wins = [t.pnl_percent for t in closed if t.pnl_percent and t.pnl_percent > 0]
    losses = [t.pnl_percent for t in closed if t.pnl_percent and t.pnl_percent < 0]

    return {
        "avg_win": sum(wins) / len(wins) if wins else 12.0,
        "avg_loss": sum(losses) / len(losses) if losses else -5.0,
    }


async def _extract_stop_loss(
    session: AsyncSession,
    user_id: int,
) -> float | None:
    """유저 원칙에서 손절 기준(%) 추출."""
    result = await session.execute(
        select(Principle).where(
            Principle.user_id == user_id,
            Principle.is_active.is_(True),
        )
    )
    principles = result.scalars().all()

    import re

    for p in principles:
        # "손절 -5%", "stop loss 5%", "손절라인 -7%" 등 패턴 매칭
        match = re.search(r"손절.*?(-?\d+(?:\.\d+)?)\s*%", p.content)
        if match:
            val = float(match.group(1))
            return val if val < 0 else -val  # 항상 음수로 반환

        match = re.search(r"stop.?loss.*?(-?\d+(?:\.\d+)?)\s*%", p.content, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            return val if val < 0 else -val

    return None
