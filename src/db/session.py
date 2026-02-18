"""Async 세션 팩토리 — SQLAlchemy 2.0 + asyncpg."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import DATABASE_URL

# Railway PostgreSQL URL: postgresql:// → postgresql+asyncpg://
_url = DATABASE_URL.strip()
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+asyncpg://", 1)

# DATABASE_URL 미설정 / 유효하지 않은 값 → sqlite async fallback (로컬 import 깨짐 방지)
if not _url or not _url.startswith(("postgresql+asyncpg://", "sqlite")):
    _url = "sqlite+aiosqlite:///tradefork_local.db"

engine: AsyncEngine = create_async_engine(
    _url,
    echo=False,
    **({} if _url.startswith("sqlite") else {
        "pool_size": 20,
        "max_overflow": 10,
        "pool_pre_ping": True,
    }),
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends용 세션 제너레이터."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
