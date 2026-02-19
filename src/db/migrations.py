"""테이블 자동 생성 — create_all."""

import logging

from src.db.models import Base
from src.db.session import _enable_wal, engine

logger = logging.getLogger(__name__)


async def create_tables() -> None:
    """모든 테이블을 DB에 생성 (이미 존재하면 스킵)."""
    await _enable_wal()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("DB 테이블 생성 완료")


async def drop_tables() -> None:
    """모든 테이블 삭제 (개발/테스트 전용)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.info("DB 테이블 삭제 완료")
