"""표현 캘리브레이션 — 유저 표현 vs 실제 수치 대조 학습.

"좀 빠진다" → -3.2%, "많이 올랐다" → +8% 등
유저 고유 표현 사전 + 스타일(톤, 깊이, 자주 쓰는 용어) 학습.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Episode, User

logger = logging.getLogger(__name__)


async def get_calibration_map(
    session: AsyncSession,
    user_id: int,
) -> dict[str, Any]:
    """유저의 표현 캘리브레이션 맵 조회.

    에피소드에 저장된 expression_calibration 데이터를 병합하여
    {"좀 빠진다": -3.2, "많이": +8.0, ...} 형태로 반환.
    """
    result = await session.execute(
        select(Episode).where(
            Episode.user_id == user_id,
            Episode.expression_calibration.isnot(None),
        ).order_by(Episode.created_at.desc())
    )
    episodes = result.scalars().all()

    cal_map: dict[str, Any] = {}
    for ep in episodes:
        cal = ep.expression_calibration
        if not isinstance(cal, dict):
            continue

        expr = cal.get("expression")
        value = cal.get("actual_value")
        if expr and value is not None:
            # 최신 값으로 덮어쓰기 (최신 에피소드가 먼저 나옴)
            if expr not in cal_map:
                cal_map[expr] = value

    return cal_map


async def get_calibration_context(
    session: AsyncSession,
    user_id: int,
) -> str:
    """캘리브레이션 맵을 LLM 컨텍스트 텍스트로 변환."""
    cal_map = await get_calibration_map(session, user_id)
    if not cal_map:
        return "캘리브레이션 데이터 없음"

    lines: list[str] = []
    for expr, value in cal_map.items():
        if isinstance(value, (int, float)):
            sign = "+" if value > 0 else ""
            lines.append(f'"{expr}" = {sign}{value}%')
        else:
            lines.append(f'"{expr}" = {value}')

    return "\n".join(lines)


async def save_calibration(
    session: AsyncSession,
    user: User,
    expression: str,
    actual_value: float,
    verified: bool = True,
) -> None:
    """캘리브레이션 데이터를 에피소드로 저장.

    chat.py의 _post_process에서 이미 에피소드로 저장하므로
    이 함수는 명시적 캘리브레이션 저장이 필요할 때만 사용.
    """
    from src.intelligence.episode import create_episode

    await create_episode(
        session,
        user,
        episode_type="chat",
        user_action=f"캘리브레이션: {expression}",
        embedding_text=f"표현 '{expression}' = {actual_value}%",
        expression_calibration={
            "expression": expression,
            "actual_value": actual_value,
            "verified": verified,
        },
    )
    logger.info(
        "캘리브레이션 저장: user=%s, '%s' = %.1f%%",
        user.telegram_id, expression, actual_value,
    )


async def get_style_context(
    session: AsyncSession,
    user: User,
) -> str:
    """유저 스타일 정보를 LLM 컨텍스트 텍스트로 변환."""
    parts: list[str] = []

    # 1) User.style_parsed (chat에서 업데이트됨)
    style = user.style_parsed or {}
    if style:
        for k, v in style.items():
            parts.append(f"{k}: {v}")

    # 2) style_raw (온보딩에서 수집된 원본 텍스트)
    if user.style_raw:
        parts.append(f"원본 스타일: {user.style_raw[:300]}")

    # 3) 에피소드 style_tags 수집 (최근 10개)
    result = await session.execute(
        select(Episode).where(
            Episode.user_id == user.id,
            Episode.style_tags.isnot(None),
        ).order_by(Episode.created_at.desc()).limit(10)
    )
    tagged_eps = result.scalars().all()

    tag_counts: dict[str, int] = {}
    for ep in tagged_eps:
        tags = ep.style_tags
        if isinstance(tags, dict):
            for tag_key, tag_val in tags.items():
                key = f"{tag_key}={tag_val}"
                tag_counts[key] = tag_counts.get(key, 0) + 1

    if tag_counts:
        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        parts.append("스타일 태그: " + ", ".join(f"{t[0]}({t[1]}회)" for t in top_tags))

    return "\n".join(parts) if parts else "스타일 정보 없음"
