"""매매 패턴 분석 — 유저의 거래 이력 기반 통계/패턴 도출.

온보딩 초기 리포트 + 싱크로율 계산 + Intelligence 컨텍스트에 사용.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Trade

logger = logging.getLogger(__name__)


async def analyze_patterns(
    session: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """유저의 매매 패턴을 DB 기반으로 분석.

    Returns:
        {
            "top_symbols": [("BTC/USDT", 15), ("ETH/USDT", 8), ...],
            "futures_ratio": 0.65,
            "avg_hold_hours": 12.3,
            "win_rate": 0.58,
            "avg_win": 8.2,
            "avg_loss": -4.1,
            "max_win": 25.0,
            "max_loss": -12.0,
            "total_trades": 50,
            "time_distribution": {"00-06": 5, "06-12": 15, ...},
            "avg_stop_loss": -5.2,
            "avg_take_profit": 10.3,
            "late_stop_ratio": 0.3,
            "early_tp_ratio": 0.2,
        }
    """
    result = await session.execute(
        select(Trade).where(Trade.user_id == user_id)
        .order_by(Trade.created_at.desc())
    )
    trades = result.scalars().all()

    if not trades:
        return _empty_patterns()

    # -- 기본 집계 --
    symbol_counter = Counter(t.symbol for t in trades)
    top_symbols = symbol_counter.most_common(5)

    total = len(trades)
    futures_count = sum(
        1 for t in trades if t.side in ("long", "short") or (t.leverage and t.leverage > 1)
    )
    futures_ratio = futures_count / total if total else 0

    # -- 승률 / PnL --
    closed = [t for t in trades if t.status == "closed" and t.pnl_percent is not None]
    wins = [t for t in closed if t.pnl_percent > 0]
    losses = [t for t in closed if t.pnl_percent < 0]

    win_rate = len(wins) / len(closed) if closed else 0
    avg_win = sum(t.pnl_percent for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_percent for t in losses) / len(losses) if losses else 0
    max_win = max((t.pnl_percent for t in wins), default=0)
    max_loss = min((t.pnl_percent for t in losses), default=0)

    # -- 보유 시간 --
    hold_hours_list: list[float] = []
    for t in closed:
        if t.opened_at and t.closed_at:
            delta = t.closed_at - t.opened_at
            hold_hours_list.append(delta.total_seconds() / 3600)

    avg_hold_hours = (
        sum(hold_hours_list) / len(hold_hours_list) if hold_hours_list else 0
    )

    # -- 시간대별 매매 빈도 --
    time_dist: dict[str, int] = {"00-06": 0, "06-12": 0, "12-18": 0, "18-24": 0}
    for t in trades:
        ts = t.opened_at or t.created_at
        if ts:
            hour = ts.hour if hasattr(ts, "hour") else 0
            if hour < 6:
                time_dist["00-06"] += 1
            elif hour < 12:
                time_dist["06-12"] += 1
            elif hour < 18:
                time_dist["12-18"] += 1
            else:
                time_dist["18-24"] += 1

    # -- 손절/익절 패턴 --
    loss_values = [t.pnl_percent for t in losses]
    win_values = [t.pnl_percent for t in wins]

    avg_stop_loss = sum(loss_values) / len(loss_values) if loss_values else 0
    avg_take_profit = sum(win_values) / len(win_values) if win_values else 0

    # "늦은 손절": 평균 손절의 2배 이상 깊은 손실
    late_stops = [v for v in loss_values if v < avg_stop_loss * 2] if avg_stop_loss < 0 else []
    late_stop_ratio = len(late_stops) / len(losses) if losses else 0

    # "빠른 익절": 평균 익절의 절반 미만에서 청산
    early_tps = [v for v in win_values if v < avg_take_profit * 0.5] if avg_take_profit > 0 else []
    early_tp_ratio = len(early_tps) / len(wins) if wins else 0

    return {
        "top_symbols": top_symbols,
        "futures_ratio": round(futures_ratio, 2),
        "avg_hold_hours": round(avg_hold_hours, 1),
        "win_rate": round(win_rate, 2),
        "avg_win": round(avg_win, 1),
        "avg_loss": round(avg_loss, 1),
        "max_win": round(max_win, 1),
        "max_loss": round(max_loss, 1),
        "total_trades": total,
        "time_distribution": time_dist,
        "avg_stop_loss": round(avg_stop_loss, 1),
        "avg_take_profit": round(avg_take_profit, 1),
        "late_stop_ratio": round(late_stop_ratio, 2),
        "early_tp_ratio": round(early_tp_ratio, 2),
    }


def format_patterns_context(patterns: dict[str, Any]) -> str:
    """패턴 분석 결과를 LLM 컨텍스트 텍스트로 변환."""
    if not patterns or patterns.get("total_trades", 0) == 0:
        return "매매 이력 없음"

    lines: list[str] = []

    # 주 매매 종목
    top = patterns.get("top_symbols", [])
    if top:
        top_str = ", ".join(f"{sym}({cnt}건)" for sym, cnt in top)
        lines.append(f"주 종목: {top_str}")

    # 선물/현물
    fr = patterns.get("futures_ratio", 0)
    lines.append(f"선물 비율: {fr*100:.0f}%")

    # 보유 시간
    hold = patterns.get("avg_hold_hours", 0)
    if hold < 1:
        lines.append(f"평균 보유: {hold*60:.0f}분 (스캘핑)")
    elif hold < 24:
        lines.append(f"평균 보유: {hold:.1f}시간 (데이트레이딩)")
    else:
        lines.append(f"평균 보유: {hold/24:.1f}일 (스윙)")

    # 성과
    wr = patterns.get("win_rate", 0)
    aw = patterns.get("avg_win", 0)
    al = patterns.get("avg_loss", 0)
    lines.append(f"승률: {wr*100:.0f}%, 평균 익절: +{aw:.1f}%, 평균 손절: {al:.1f}%")
    lines.append(
        f"최대: +{patterns.get('max_win', 0):.1f}% / {patterns.get('max_loss', 0):.1f}%"
    )

    # 시간대
    td = patterns.get("time_distribution", {})
    if td:
        peak = max(td, key=td.get) if td else ""
        lines.append(f"주 매매 시간대: {peak} ({td.get(peak, 0)}건)")

    # 손절/익절 습관
    lsr = patterns.get("late_stop_ratio", 0)
    etr = patterns.get("early_tp_ratio", 0)
    habits: list[str] = []
    if lsr > 0.3:
        habits.append(f"늦은 손절 경향 ({lsr*100:.0f}%)")
    if etr > 0.3:
        habits.append(f"빠른 익절 경향 ({etr*100:.0f}%)")
    if habits:
        lines.append("습관: " + ", ".join(habits))

    return "\n".join(lines)


def _empty_patterns() -> dict[str, Any]:
    """매매 이력이 없을 때 기본 패턴."""
    return {
        "top_symbols": [],
        "futures_ratio": 0,
        "avg_hold_hours": 0,
        "win_rate": 0,
        "avg_win": 0,
        "avg_loss": 0,
        "max_win": 0,
        "max_loss": 0,
        "total_trades": 0,
        "time_distribution": {"00-06": 0, "06-12": 0, "12-18": 0, "18-24": 0},
        "avg_stop_loss": 0,
        "avg_take_profit": 0,
        "late_stop_ratio": 0,
        "early_tp_ratio": 0,
    }
