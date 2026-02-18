"""유저 등록 + 거래소 API 연결."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import PRO_MAX_EXCHANGES
from src.db.models import ExchangeConnection, User
from src.security.encryption import encrypt

logger = logging.getLogger(__name__)


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
) -> tuple[User, bool]:
    """유저 조회 또는 생성. Returns (user, is_new)."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if user is not None:
        user.last_active_at = datetime.now(timezone.utc)
        if username and user.username != username:
            user.username = username
        await session.flush()
        return user, False

    user = User(
        telegram_id=telegram_id,
        username=username,
        onboarding_step=0,
        last_active_at=datetime.now(timezone.utc),
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user, True


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """telegram_id로 유저 조회."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def connect_exchange(
    session: AsyncSession,
    user: User,
    exchange_name: str,
    api_key: str,
    api_secret: str,
) -> str | None:
    """거래소 API 연결. 성공 시 None, 실패 시 에러 메시지."""
    # 최대 3개 제한
    result = await session.execute(
        select(ExchangeConnection).where(
            ExchangeConnection.user_id == user.id,
            ExchangeConnection.is_active.is_(True),
        )
    )
    active = result.scalars().all()
    if len(active) >= PRO_MAX_EXCHANGES:
        return f"거래소는 최대 {PRO_MAX_EXCHANGES}개까지 연결 가능해."

    # 이미 연결된 거래소 체크
    for conn in active:
        if conn.exchange == exchange_name:
            return f"{exchange_name}은 이미 연결되어 있어."

    # ccxt로 연결 테스트
    try:
        import aiohttp
        from aiohttp.resolver import ThreadedResolver

        import ccxt.async_support as ccxt_async

        exchange_cls = getattr(ccxt_async, exchange_name, None)
        if exchange_cls is None:
            return f"지원하지 않는 거래소: {exchange_name}"

        # Windows aiodns DNS 해석 실패 방지 → ThreadedResolver 사용
        connector = aiohttp.TCPConnector(resolver=ThreadedResolver())
        http_session = aiohttp.ClientSession(connector=connector)
        ex = exchange_cls({"apiKey": api_key, "secret": api_secret, "session": http_session})
        try:
            await ex.fetch_balance()
        finally:
            await ex.close()
            await http_session.close()
    except Exception as e:
        err = str(e)
        # API 키 노출 방지
        if api_key in err:
            err = err.replace(api_key, "***")
        if api_secret in err:
            err = err.replace(api_secret, "***")
        logger.warning("거래소 연결 실패: %s — %s", exchange_name, err)
        return f"연결 실패: {err}\n\nAPI Key와 Secret을 다시 확인해봐."

    # 암호화 저장
    conn = ExchangeConnection(
        user_id=user.id,
        exchange=exchange_name,
        api_key_encrypted=encrypt(api_key),
        api_secret_encrypted=encrypt(api_secret),
        last_checked_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    logger.info("거래소 연결 완료: user=%s, exchange=%s", user.telegram_id, exchange_name)
    return None
