"""Async 세션 팩토리 — SQLAlchemy 2.0 + asyncpg."""

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.config import DATABASE_URL

# Railway PostgreSQL URL: postgresql:// → postgresql+asyncpg://
_url = DATABASE_URL.strip()
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+asyncpg://", 1)

# DATABASE_URL 미설정 / 유효하지 않은 값 → sqlite async fallback (로컬 import 깨짐 방지)
_is_sqlite = False
if not _url or not _url.startswith(("postgresql+asyncpg://", "sqlite")):
    _url = "sqlite+aiosqlite:///tradefork_local.db"
    _is_sqlite = True
elif _url.startswith("sqlite"):
    _is_sqlite = True

# SQLite: StaticPool (단일 커넥션 공유) — 동시성 문제 해결
engine: AsyncEngine = create_async_engine(
    _url,
    echo=False,
    **(
        {
            "connect_args": {"check_same_thread": False, "timeout": 30},
            "poolclass": StaticPool,
        }
        if _is_sqlite
        else {
            "pool_size": 20,
            "max_overflow": 10,
            "pool_pre_ping": True,
        }
    ),
)


async def _enable_wal() -> None:
    """SQLite WAL 모드 활성화 — 동시 읽기/쓰기 허용."""
    if not _is_sqlite:
        return
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA busy_timeout=5000"))

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
