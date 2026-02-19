"""테이블 자동 생성 — create_all."""

import logging

from sqlalchemy import text

from src.db.models import Base
from src.db.session import _enable_wal, _is_sqlite, engine

logger = logging.getLogger(__name__)


async def _migrate_timestamps_to_tz() -> None:
    """PostgreSQL: TIMESTAMP → TIMESTAMPTZ 마이그레이션 (한 번만 실행)."""
    if _is_sqlite:
        return

    # 변경 대상: (테이블, 컬럼) 목록
    columns = [
        ("users", "daily_signal_reset_at"),
        ("users", "last_active_at"),
        ("users", "created_at"),
        ("users", "updated_at"),
        ("exchange_connections", "last_checked_at"),
        ("exchange_connections", "created_at"),
        ("episodes", "created_at"),
        ("episodes", "updated_at"),
        ("principles", "created_at"),
        ("trades", "opened_at"),
        ("trades", "closed_at"),
        ("trades", "created_at"),
        ("base_streams", "last_mentioned_at"),
        ("base_streams", "created_at"),
        ("base_streams", "updated_at"),
        ("user_triggers", "triggered_at"),
        ("user_triggers", "created_at"),
        ("signals", "created_at"),
        ("chat_messages", "created_at"),
        ("patrol_logs", "created_at"),
    ]

    async with engine.begin() as conn:
        for table, col in columns:
            try:
                await conn.execute(text(
                    f"ALTER TABLE {table} ALTER COLUMN {col} "
                    f"TYPE TIMESTAMPTZ USING {col} AT TIME ZONE 'UTC'"
                ))
            except Exception:
                pass  # 이미 TIMESTAMPTZ이거나 테이블 미존재 → 무시
    logger.info("TIMESTAMP → TIMESTAMPTZ 마이그레이션 완료")


async def _migrate_add_briefing_hour() -> None:
    """users 테이블에 briefing_hour 컬럼 추가."""
    if _is_sqlite:
        return
    async with engine.begin() as conn:
        try:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN briefing_hour INTEGER DEFAULT 8"
            ))
        except Exception:
            pass  # 이미 존재
    logger.info("briefing_hour 마이그레이션 완료")


async def _migrate_add_confidence_axes() -> None:
    """signals 테이블에 3축 confidence 컬럼 추가."""
    if _is_sqlite:
        return
    cols = ["confidence_style", "confidence_history", "confidence_market"]
    async with engine.begin() as conn:
        for col_name in cols:
            try:
                await conn.execute(text(
                    f"ALTER TABLE signals ADD COLUMN {col_name} FLOAT"
                ))
            except Exception:
                pass  # 이미 존재
    logger.info("confidence 3축 마이그레이션 완료")


async def create_tables() -> None:
    """모든 테이블을 DB에 생성 (이미 존재하면 스킵)."""
    await _enable_wal()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("DB 테이블 생성 완료")
    await _migrate_timestamps_to_tz()
    await _migrate_add_briefing_hour()
    await _migrate_add_confidence_axes()


async def drop_tables() -> None:
    """모든 테이블 삭제 (개발/테스트 전용)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.info("DB 테이블 삭제 완료")
