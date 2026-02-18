"""Q1 매매 감지 — 거래소에서 새 매매 감지 + FORKER 근거 추론.

시나리오 D 흐름:
  매매 감지 → 자동 필터 → FORKER 근거 추론(Opus) → 유저 확인 [맞아/아니야]
  → 보유 중 코멘터리 → 청산 감지 → 결과 기록 + 손실 시 복기 제안
  → Intelligence 에피소드 영구 저장
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from src.bot.keyboards import confirm_reasoning
from src.config import DUST_THRESHOLD_PERCENT
from src.db.models import ChatMessage, ExchangeConnection, Trade, User
from src.exchange.manager import get_exchange, get_user_connections
from src.intelligence.episode import create_episode
from src.llm.client import llm_client
from src.llm.prompts import TRADE_REASONING_PROMPT, TRADE_CLOSE_PROMPT

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 필터
# ------------------------------------------------------------------
def _is_dust_trade(trade: dict, total_balance_value: float) -> bool:
    """극소액(잔고 1% 미만) 거래인지 판별."""
    cost = trade.get("cost") or 0
    if not cost:
        info = trade.get("info", {})
        cost = float(info.get("executed_funds") or info.get("cummulativeQuoteQty") or 0)
    else:
        cost = float(cost)

    if total_balance_value <= 0 or cost <= 0:
        return True

    return (cost / total_balance_value) * 100 < DUST_THRESHOLD_PERCENT


def _is_deposit_or_withdrawal(trade: dict) -> bool:
    """입출금 거래인지 판별."""
    trade_type = (trade.get("type") or "").lower()
    if trade_type in ("deposit", "withdrawal", "transfer"):
        return True
    info = trade.get("info", {})
    if info.get("type") in ("deposit", "withdrawal"):
        return True
    return False


# ------------------------------------------------------------------
# 새 매매 감지
# ------------------------------------------------------------------
async def poll_trades(
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> int:
    """연결된 모든 거래소에서 새 매매 감지.

    Returns:
        감지된 새 매매 수
    """
    connections = await get_user_connections(session, user.id)
    if not connections:
        return 0

    detected = 0

    for conn in connections:
        try:
            # 마지막 체크 이후 거래 가져오기
            last_checked = conn.last_checked_at or (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            )
            since_ms = int(last_checked.timestamp() * 1000)

            async with get_exchange(session, user.id, conn.exchange) as ex:
                # 잔고 조회 (dust 필터용)
                try:
                    bal = await ex.fetch_balance()
                    total_value = sum(
                        float(v) for v in bal.get("total", {}).values()
                        if v and isinstance(v, (int, float))
                    )
                except Exception:
                    total_value = 0

                # 새 거래 조회
                from src.core.onboarding import _fetch_all_orders

                new_trades = await _fetch_all_orders(ex, conn.exchange, since_ms)

            # 필터 적용
            for trade_data in new_trades:
                ts = trade_data.get("timestamp", 0)
                if ts <= since_ms:
                    continue

                if _is_deposit_or_withdrawal(trade_data):
                    logger.debug("입출금 스킵: %s", trade_data.get("symbol"))
                    continue

                if _is_dust_trade(trade_data, total_value):
                    logger.debug("극소액 스킵: %s", trade_data.get("symbol"))
                    continue

                # 새 매매 처리
                await handle_new_trade(session, user, trade_data, conn.exchange, bot)
                detected += 1

            # 마지막 체크 시간 갱신
            conn.last_checked_at = datetime.now(timezone.utc)
            await session.flush()

        except Exception:
            logger.error(
                "매매 감지 실패: user=%s, exchange=%s",
                user.telegram_id,
                conn.exchange,
                exc_info=True,
            )

    return detected


# ------------------------------------------------------------------
# 새 진입 처리
# ------------------------------------------------------------------
async def handle_new_trade(
    session: AsyncSession,
    user: User,
    trade_data: dict[str, Any],
    exchange_name: str,
    bot: Bot,
) -> None:
    """매매 진입 감지 → DB 기록 + FORKER 근거 추론 + 유저 확인 요청."""
    symbol = trade_data.get("symbol", "UNKNOWN")
    side = trade_data.get("side", "buy")
    amount = float(trade_data.get("amount") or 0)
    cost = trade_data.get("cost") or 0
    if not cost:
        info = trade_data.get("info", {})
        cost = float(info.get("executed_funds") or info.get("cummulativeQuoteQty") or 0)
    else:
        cost = float(cost)
    entry_price = cost / amount if amount > 0 else 0
    ts = trade_data.get("timestamp", 0)
    opened_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(timezone.utc)

    # 같은 매매 중복 체크 (symbol + side + timestamp 근접)
    existing = await session.execute(
        select(Trade).where(
            Trade.user_id == user.id,
            Trade.symbol == symbol,
            Trade.exchange == exchange_name,
            Trade.opened_at >= opened_at - timedelta(seconds=10),
            Trade.opened_at <= opened_at + timedelta(seconds=10),
        )
    )
    if existing.scalar_one_or_none():
        return  # 이미 처리됨

    # 1) Trade DB 기록
    trade = Trade(
        user_id=user.id,
        exchange=exchange_name,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        size=amount,
        leverage=1.0,
        status="open",
        opened_at=opened_at,
    )
    session.add(trade)
    await session.flush()

    # 2) FORKER 근거 추론 (Opus)
    reasoning = await _infer_trade_reasoning(session, user, trade)
    trade.forker_reasoning = reasoning
    await session.flush()

    # 3) 유저에게 확인 요청
    direction = "롱" if side in ("buy", "long") else "숏"
    msg = (
        f"🔄 {symbol} {direction} 감지!\n\n"
        f"금액: {cost:,.0f} | 수량: {amount}\n\n"
        f"{reasoning}\n\n"
        f"맞지?"
    )

    session.add(
        ChatMessage(
            user_id=user.id,
            role="assistant",
            content=msg,
            message_type="text",
            intent="trade_reasoning",
            metadata_={"trade_id": trade.id},
        )
    )
    await session.commit()

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=msg,
            reply_markup=confirm_reasoning(),
        )
    except Exception:
        logger.error("매매 감지 알림 전송 실패: %s", user.telegram_id, exc_info=True)
    logger.info(
        "매매 감지: user=%s, %s %s %s @ %s",
        user.telegram_id,
        exchange_name,
        symbol,
        side,
        entry_price,
    )


async def _infer_trade_reasoning(
    session: AsyncSession,
    user: User,
    trade: Trade,
) -> str:
    """FORKER가 유저의 매매 근거를 추론 (Opus)."""
    # Intelligence 컨텍스트 구축
    from src.intelligence.episode import get_recent_episodes

    recent_eps = await get_recent_episodes(session, user.id, limit=5)
    ep_context = "\n".join(
        f"- [{e.episode_type}] {e.user_action}" for e in recent_eps
    ) or "없음"

    # 원칙 조회
    from src.db.models import Principle

    result = await session.execute(
        select(Principle).where(
            Principle.user_id == user.id,
            Principle.is_active.is_(True),
        )
    )
    principles = result.scalars().all()
    principles_text = "\n".join(
        f"{i+1}. {p.content}" for i, p in enumerate(principles)
    ) or "없음"

    style = user.style_parsed or {}
    style_text = ", ".join(f"{k}: {v}" for k, v in style.items()) if style else "없음"

    prompt_text = TRADE_REASONING_PROMPT.format(
        symbol=trade.symbol,
        side=trade.side,
        entry_price=trade.entry_price,
        size=trade.size,
        exchange=trade.exchange,
        episodes=ep_context,
        principles=principles_text,
        style=style_text,
    )

    try:
        resp = await llm_client.trade_reasoning(
            system=prompt_text,
            messages=[{
                "role": "user",
                "content": f"{trade.symbol} {trade.side} 진입 — 이 유저가 왜 이 시점에 이 매매를 했을지 추론해.",
            }],
            max_tokens=500,
        )
        return resp.text.strip()
    except Exception:
        logger.error("근거 추론 LLM 실패", exc_info=True)
        return "근거 추론에 실패했어. 직접 알려줄래?"


# ------------------------------------------------------------------
# 청산 감지
# ------------------------------------------------------------------
async def detect_closed_trades(
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    """기존 open 포지션이 청산됐는지 확인 → 결과 기록."""
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user.id,
            Trade.status == "open",
        )
    )
    open_trades = result.scalars().all()
    if not open_trades:
        return

    connections = await get_user_connections(session, user.id)
    exchange_names = {c.exchange for c in connections}

    for trade in open_trades:
        if trade.exchange not in exchange_names:
            continue

        try:
            async with get_exchange(session, user.id, trade.exchange) as ex:
                # 현재 잔고에서 해당 코인 확인
                bal = await ex.fetch_balance()
                base_symbol = trade.symbol.split("/")[0]
                remaining = float(bal.get("total", {}).get(base_symbol, 0) or 0)

                # 90% 이상 소진 → 청산으로 판정
                if remaining < trade.size * 0.1:
                    # 현재가로 청산가 추정
                    try:
                        ticker = await ex.fetch_ticker(trade.symbol)
                        exit_price = ticker.get("last", 0)
                    except Exception:
                        exit_price = 0

                    if exit_price and trade.entry_price:
                        if trade.side in ("buy", "long"):
                            pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100
                        else:
                            pnl_pct = ((trade.entry_price - exit_price) / trade.entry_price) * 100
                    else:
                        pnl_pct = 0

                    await handle_trade_close(session, user, trade, pnl_pct, exit_price, bot)
        except Exception:
            logger.error(
                "청산 감지 실패: trade=%s, symbol=%s",
                trade.id,
                trade.symbol,
                exc_info=True,
            )


async def handle_trade_close(
    session: AsyncSession,
    user: User,
    trade: Trade,
    pnl_percent: float,
    exit_price: float,
    bot: Bot,
) -> None:
    """청산 감지 → 결과 기록 + 코멘터리 + 복기 제안."""

    # 1) Trade 업데이트
    trade.status = "closed"
    trade.exit_price = exit_price
    trade.pnl_percent = pnl_percent
    trade.pnl_amount = (exit_price - trade.entry_price) * trade.size if exit_price and trade.entry_price else 0
    trade.closed_at = datetime.now(timezone.utc)
    await session.flush()

    # 2) 평균 익절/손절 조회
    avg_stats = await _get_trade_stats(session, user.id)

    # 3) 코멘터리 생성 (LLM)
    commentary = await _generate_close_commentary(
        trade, pnl_percent, avg_stats,
    )

    # 4) 위험 감지
    risk_warning = await _check_risk_patterns(session, user)

    # 5) 메시지 조합
    if pnl_percent >= 0:
        emoji = "📈"
        msg = f"{emoji} {trade.symbol} +{pnl_percent:.1f}%!\n\n{commentary}"
    else:
        emoji = "📉"
        msg = (
            f"{emoji} {trade.symbol} {pnl_percent:.1f}%\n\n"
            f"{commentary}\n\n"
            f"같이 복기해볼까?\n"
            f"① 진입 근거: {trade.forker_reasoning or '미확인'}\n"
            f"② 결과: {pnl_percent:.1f}%\n"
        )

    if risk_warning:
        msg += f"\n\n⚠️ {risk_warning}"

    # 6) 시그널→매매 결과 자동 피드백 (Q4)
    try:
        from src.feedback.processor import process_trade_result_feedback

        await process_trade_result_feedback(session, user, trade)
    except Exception:
        logger.warning("매매 결과 피드백 처리 실패", exc_info=True)

    # 7) 에피소드 저장
    episode = await create_episode(
        session,
        user,
        episode_type="trade",
        user_action=f"{trade.symbol} {trade.side} 청산: {pnl_percent:+.1f}%",
        embedding_text=(
            f"{trade.symbol} {trade.side} 진입가 {trade.entry_price} "
            f"청산가 {exit_price} 결과 {pnl_percent:+.1f}% "
            f"근거: {trade.forker_reasoning or '미확인'}"
        ),
        trade_data={
            "symbol": trade.symbol,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "exit_price": exit_price,
            "pnl_percent": pnl_percent,
        },
        trade_result={"pnl_percent": pnl_percent, "exit_price": exit_price},
        reasoning=trade.forker_reasoning,
    )
    trade.episode_id = episode.id

    session.add(
        ChatMessage(
            user_id=user.id,
            role="assistant",
            content=msg,
            message_type="text",
            intent="trade_close",
        )
    )
    await session.commit()

    try:
        await bot.send_message(chat_id=user.telegram_id, text=msg)
    except Exception:
        logger.error("청산 알림 전송 실패: %s", user.telegram_id, exc_info=True)
    logger.info(
        "청산 기록: user=%s, %s %s %.1f%%",
        user.telegram_id,
        trade.symbol,
        trade.side,
        pnl_percent,
    )


# ------------------------------------------------------------------
# 보조 함수
# ------------------------------------------------------------------
async def _get_trade_stats(
    session: AsyncSession,
    user_id: int,
) -> dict[str, float]:
    """유저의 평균 익절/손절/승률 통계."""
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.status == "closed",
            Trade.pnl_percent.isnot(None),
        )
    )
    closed = result.scalars().all()
    if not closed:
        return {"avg_win": 0, "avg_loss": 0, "win_rate": 0, "total": 0}

    wins = [t.pnl_percent for t in closed if t.pnl_percent and t.pnl_percent > 0]
    losses = [t.pnl_percent for t in closed if t.pnl_percent and t.pnl_percent < 0]

    return {
        "avg_win": sum(wins) / len(wins) if wins else 0,
        "avg_loss": sum(losses) / len(losses) if losses else 0,
        "win_rate": len(wins) / len(closed) * 100 if closed else 0,
        "total": len(closed),
    }


async def _generate_close_commentary(
    trade: Trade,
    pnl_percent: float,
    stats: dict[str, float],
) -> str:
    """청산 코멘터리 생성 (LLM)."""
    try:
        resp = await llm_client.chat(
            system=TRADE_CLOSE_PROMPT.format(
                symbol=trade.symbol,
                side=trade.side,
                entry_price=trade.entry_price or 0,
                exit_price=trade.exit_price or 0,
                pnl=pnl_percent,
                reasoning=trade.forker_reasoning or "미확인",
                avg_win=stats["avg_win"],
                avg_loss=stats["avg_loss"],
                win_rate=stats["win_rate"],
            ),
            messages=[{"role": "user", "content": "코멘터리 생성"}],
            max_tokens=300,
        )
        return resp.text.strip()
    except Exception:
        logger.warning("코멘터리 LLM 실패")
        if pnl_percent >= 0 and stats["avg_win"] > 0:
            return f"너 평균 익절 {stats['avg_win']:.1f}%야."
        return ""


async def _check_risk_patterns(
    session: AsyncSession,
    user: User,
) -> str | None:
    """위험 패턴 감지: 연속 손실, FOMO."""
    # 최근 5개 매매 확인
    result = await session.execute(
        select(Trade)
        .where(
            Trade.user_id == user.id,
            Trade.status == "closed",
            Trade.pnl_percent.isnot(None),
        )
        .order_by(Trade.closed_at.desc())
        .limit(5)
    )
    recent = result.scalars().all()

    # 연속 3회 이상 손실
    consecutive_losses = 0
    for t in recent:
        if t.pnl_percent and t.pnl_percent < 0:
            consecutive_losses += 1
        else:
            break

    if consecutive_losses >= 3:
        return f"연속 {consecutive_losses}회 손실이야. 쉬어가는 것도 전략이야."

    # FOMO 감지: 최근 1시간 내 3건 이상 진입
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    result = await session.execute(
        select(func.count()).select_from(Trade).where(
            Trade.user_id == user.id,
            Trade.opened_at >= one_hour_ago,
        )
    )
    recent_count = result.scalar() or 0
    if recent_count >= 3:
        return "1시간 안에 3건 이상 매매했어. 과매매 아닌지 한번 생각해봐."

    return None


# ------------------------------------------------------------------
# 근거 확인 콜백 처리
# ------------------------------------------------------------------
async def confirm_trade_reasoning(
    session: AsyncSession,
    user: User,
    confirmed: bool,
) -> None:
    """유저가 매매 근거를 확인/부정했을 때 처리.

    맞아 → forker_reasoning 저장, user_confirmed=True, 에피소드 생성
    아니야 → user_confirmed=False (이후 유저 입력으로 실제 근거 저장)
    """
    # 가장 최근 open 매매 (근거 미확인)
    result = await session.execute(
        select(Trade)
        .where(
            Trade.user_id == user.id,
            Trade.user_confirmed_reasoning.is_(None),
        )
        .order_by(Trade.created_at.desc())
        .limit(1)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        return

    trade.user_confirmed_reasoning = confirmed

    if confirmed:
        # 에피소드 생성
        await create_episode(
            session,
            user,
            episode_type="trade",
            user_action=f"{trade.symbol} {trade.side} 진입",
            embedding_text=(
                f"{trade.symbol} {trade.side} @ {trade.entry_price} "
                f"근거: {trade.forker_reasoning}"
            ),
            trade_data={
                "symbol": trade.symbol,
                "side": trade.side,
                "entry_price": trade.entry_price,
                "exchange": trade.exchange,
            },
            reasoning=trade.forker_reasoning,
        )
    await session.flush()


async def save_user_reasoning(
    session: AsyncSession,
    user: User,
    reason_text: str,
) -> None:
    """유저가 직접 입력한 매매 근거 저장 (reason:no 후 자연어 입력)."""
    result = await session.execute(
        select(Trade)
        .where(
            Trade.user_id == user.id,
            Trade.user_confirmed_reasoning.is_(False),
            Trade.user_actual_reasoning.is_(None),
        )
        .order_by(Trade.created_at.desc())
        .limit(1)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        return

    trade.user_actual_reasoning = reason_text

    # 유저 실제 근거로 에피소드 생성
    await create_episode(
        session,
        user,
        episode_type="trade",
        user_action=f"{trade.symbol} {trade.side} 진입 (유저 근거)",
        embedding_text=(
            f"{trade.symbol} {trade.side} @ {trade.entry_price} "
            f"유저 근거: {reason_text}"
        ),
        trade_data={
            "symbol": trade.symbol,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "exchange": trade.exchange,
        },
        reasoning=reason_text,
    )
    await session.flush()
    logger.info(
        "유저 매매 근거 저장: user=%s, trade=%d",
        user.telegram_id, trade.id,
    )
