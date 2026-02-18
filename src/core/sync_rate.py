"""싱크로율 계산 — 학습 완성도 40% + 판단 일치율 60%."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    ChatMessage,
    Episode,
    ExchangeConnection,
    Principle,
    Signal,
    Trade,
    User,
)

logger = logging.getLogger(__name__)


async def calculate_sync_rate(session: AsyncSession, user: User) -> dict[str, Any]:
    """싱크로율 전체 계산.

    Returns:
        {sync_rate, learning, learning_details, judge, judge_details}
    """
    uid = user.id
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    # ---- 학습 완성도 (각 항목 가중치) ----
    # 거래소 연결 수: connected/3 × 25%
    ex_count_row = await session.execute(
        select(func.count()).where(
            ExchangeConnection.user_id == uid,
            ExchangeConnection.is_active.is_(True),
        )
    )
    ex_count = ex_count_row.scalar() or 0
    ex_score = min(ex_count / 3, 1.0) * 25

    # 투자 원칙: min(count/5, 1) × 25%
    pr_count_row = await session.execute(
        select(func.count()).where(
            Principle.user_id == uid, Principle.is_active.is_(True)
        )
    )
    pr_count = pr_count_row.scalar() or 0
    pr_score = min(pr_count / 5, 1.0) * 25

    # 누적 에피소드: min(count/50, 1) × 30%
    ep_count_row = await session.execute(
        select(func.count()).where(Episode.user_id == uid)
    )
    ep_count = ep_count_row.scalar() or 0
    ep_score = min(ep_count / 50, 1.0) * 30

    # 대화 빈도: min(recent_7d/20, 1) × 20%
    msg_count_row = await session.execute(
        select(func.count()).where(
            ChatMessage.user_id == uid,
            ChatMessage.role == "user",
            ChatMessage.created_at >= week_ago,
        )
    )
    msg_count = msg_count_row.scalar() or 0
    msg_score = min(msg_count / 20, 1.0) * 20

    learning = round(ex_score + pr_score + ep_score + msg_score, 1)

    # ---- 판단 일치율 ----
    sig_total_row = await session.execute(
        select(func.count()).where(Signal.user_id == uid, Signal.user_agreed.isnot(None))
    )
    sig_total = sig_total_row.scalar() or 0

    judge = 0.0
    judge_details: dict[str, Any] = {}

    if sig_total >= 5:
        # 시그널 동의율 × 40%
        agreed_row = await session.execute(
            select(func.count()).where(
                Signal.user_id == uid, Signal.user_agreed.is_(True)
            )
        )
        agreed = agreed_row.scalar() or 0
        agree_pct = (agreed / sig_total * 100) if sig_total else 0

        # 시그널 후 실제 매매 일치 × 30%
        followed_row = await session.execute(
            select(func.count()).where(
                Signal.user_id == uid, Signal.trade_followed.is_(True)
            )
        )
        followed = followed_row.scalar() or 0
        follow_pct = (followed / sig_total * 100) if sig_total else 0

        # 근거 추론 적중률 × 30%
        reasoning_total_row = await session.execute(
            select(func.count()).where(
                Trade.user_id == uid,
                Trade.user_confirmed_reasoning.isnot(None),
            )
        )
        reasoning_total = reasoning_total_row.scalar() or 0
        correct_row = await session.execute(
            select(func.count()).where(
                Trade.user_id == uid, Trade.user_confirmed_reasoning.is_(True)
            )
        )
        correct = correct_row.scalar() or 0
        reason_pct = (correct / reasoning_total * 100) if reasoning_total else 0

        judge = round(agree_pct * 0.4 + follow_pct * 0.3 + reason_pct * 0.3, 1)
        judge_details = {
            "agreed": agreed,
            "sig_total": sig_total,
            "agree_pct": round(agree_pct, 1),
            "followed": followed,
            "follow_pct": round(follow_pct, 1),
            "correct_reasoning": correct,
            "reasoning_total": reasoning_total,
            "reason_pct": round(reason_pct, 1),
        }
    else:
        judge_details = {"sig_total": sig_total, "insufficient": True}

    sync_rate = round(learning * 0.4 + judge * 0.6, 1)

    return {
        "sync_rate": sync_rate,
        "learning": learning,
        "learning_details": {
            "ex_count": ex_count,
            "pr_count": pr_count,
            "ep_count": ep_count,
            "msg_7d": msg_count,
        },
        "judge": judge,
        "judge_details": judge_details,
    }


def format_sync_rate(data: dict[str, Any]) -> str:
    """싱크로율 결과를 텔레그램 메시지 텍스트로 포매팅."""
    ld = data["learning_details"]
    jd = data["judge_details"]

    lines = [
        f"🔄 싱크로율: {data['sync_rate']}%",
        f"📚 학습 완성도: {data['learning']}%",
        f"  · 거래소 연결: {ld['ex_count']}/3",
        f"  · 에피소드: {ld['ep_count']}개",
        f"  · 투자 원칙: {ld['pr_count']}개 설정됨",
    ]

    if jd.get("insufficient"):
        lines.append(f"🎯 판단 일치율: 아직 데이터 수집 중... ({jd['sig_total']}/5)")
    else:
        lines.append(f"🎯 판단 일치율: {data['judge']}%")
        lines.append(
            f"  · 시그널 동의율: {jd['agreed']}/{jd['sig_total']} ({jd['agree_pct']}%)"
        )
        lines.append(
            f"  · 근거 추론 적중: {jd['correct_reasoning']}/{jd['reasoning_total']} ({jd['reason_pct']}%)"
        )

    lines.append("")
    lines.append("💡 피드백을 자주 해주면 FORKER가 더 빨리 배워!")
    return "\n".join(lines)
