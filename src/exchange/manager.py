"""ccxt 기반 거래소 통합 매니저.

바이낸스/업비트/빗썸 통합 관리:
- 암호화된 키 복호화 → ccxt 인스턴스 생성 → 메모리에서만 사용
- 업비트/빗썸: 현물만, KRW 마켓
- 김프(한국 프리미엄) 계산
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator

import aiohttp
import httpx
from aiohttp.resolver import ThreadedResolver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import ccxt.async_support as ccxt_async

from src.db.models import ExchangeConnection
from src.security.encryption import decrypt

logger = logging.getLogger(__name__)

# USD/KRW 환율 캐시 (김프 계산용)
_usd_krw_rate: float = 0.0
_usd_krw_fetched_at: float = 0.0


async def _get_usd_krw_rate() -> float:
    """USD/KRW 환율 조회 (캐시 30분)."""
    import time

    global _usd_krw_rate, _usd_krw_fetched_at
    if _usd_krw_rate > 0 and (time.time() - _usd_krw_fetched_at) < 1800:
        return _usd_krw_rate

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.exchangerate-api.com/v4/latest/USD"
            )
            resp.raise_for_status()
            data = resp.json()
            _usd_krw_rate = data["rates"].get("KRW", 1350.0)
            _usd_krw_fetched_at = time.time()
    except Exception:
        logger.warning("USD/KRW 환율 조회 실패, 기본값 1350 사용")
        if _usd_krw_rate == 0:
            _usd_krw_rate = 1350.0
    return _usd_krw_rate


def _create_exchange(
    exchange_name: str,
    api_key: str,
    api_secret: str,
) -> tuple[Any, aiohttp.ClientSession]:
    """ccxt 인스턴스 + aiohttp 세션 생성.

    Returns:
        (ccxt_exchange, http_session) — 사용 후 반드시 close 필요
    """
    exchange_cls = getattr(ccxt_async, exchange_name, None)
    if exchange_cls is None:
        raise ValueError(f"지원하지 않는 거래소: {exchange_name}")

    connector = aiohttp.TCPConnector(resolver=ThreadedResolver())
    http_session = aiohttp.ClientSession(connector=connector)
    ex = exchange_cls({
        "apiKey": api_key,
        "secret": api_secret,
        "session": http_session,
    })
    return ex, http_session


@asynccontextmanager
async def get_exchange(
    session: AsyncSession,
    user_id: int,
    exchange_name: str,
) -> AsyncGenerator[Any, None]:
    """DB에서 암호화 키 복호화 → ccxt 인스턴스 context manager.

    사용법::

        async with get_exchange(session, user_id, "binance") as ex:
            balance = await ex.fetch_balance()

    복호화된 키는 메모리에서만 사용, 사용 후 즉시 폐기.
    """
    result = await session.execute(
        select(ExchangeConnection).where(
            ExchangeConnection.user_id == user_id,
            ExchangeConnection.exchange == exchange_name,
            ExchangeConnection.is_active.is_(True),
        )
    )
    conn = result.scalar_one_or_none()
    if conn is None:
        raise ValueError(f"연결된 {exchange_name} 없음 (user_id={user_id})")

    api_key = decrypt(conn.api_key_encrypted)
    api_secret = decrypt(conn.api_secret_encrypted)
    ex, http_session = _create_exchange(exchange_name, api_key, api_secret)
    del api_key, api_secret  # 메모리 즉시 폐기

    try:
        await ex.load_markets()
        yield ex
    finally:
        await ex.close()
        await http_session.close()


async def get_user_connections(
    session: AsyncSession,
    user_id: int,
) -> list[ExchangeConnection]:
    """유저의 활성 거래소 연결 목록."""
    result = await session.execute(
        select(ExchangeConnection).where(
            ExchangeConnection.user_id == user_id,
            ExchangeConnection.is_active.is_(True),
        )
    )
    return list(result.scalars().all())


# ------------------------------------------------------------------
# 잔고 조회
# ------------------------------------------------------------------
async def fetch_balance(
    session: AsyncSession,
    user_id: int,
    exchange_name: str,
) -> dict[str, float]:
    """거래소 잔고 조회 → {asset: amount}. 0 이상만 반환."""
    async with get_exchange(session, user_id, exchange_name) as ex:
        bal = await ex.fetch_balance()
        result: dict[str, float] = {}
        for asset, amount in bal.get("total", {}).items():
            if amount and float(amount) > 0:
                result[asset] = float(amount)
        return result


# ------------------------------------------------------------------
# 포지션 조회 (선물)
# ------------------------------------------------------------------
async def fetch_open_positions(
    session: AsyncSession,
    user_id: int,
    exchange_name: str,
) -> list[dict[str, Any]]:
    """오픈 포지션 조회 (선물 거래소만).

    업비트/빗썸은 선물 없으므로 잔고에서 현물 포지션 유추.
    """
    if exchange_name in ("upbit", "bithumb"):
        # 현물: 잔고에서 보유 코인 = 포지션
        balance = await fetch_balance(session, user_id, exchange_name)
        positions: list[dict[str, Any]] = []
        quote = "KRW"
        for asset, amount in balance.items():
            if asset in ("KRW", "USDT", "BUSD", "BTC"):
                continue
            positions.append({
                "symbol": f"{asset}/{quote}",
                "side": "long",
                "size": amount,
                "entry_price": None,  # 현물은 진입가 추적 불가
                "pnl": None,
                "leverage": 1.0,
                "exchange": exchange_name,
            })
        return positions

    # 바이낸스 선물
    async with get_exchange(session, user_id, exchange_name) as ex:
        try:
            raw_positions = await ex.fetch_positions()
        except Exception:
            logger.warning("포지션 조회 실패: %s", exchange_name)
            return []

        positions = []
        for p in raw_positions:
            size = float(p.get("contracts", 0) or 0)
            if size == 0:
                continue
            positions.append({
                "symbol": p.get("symbol", ""),
                "side": p.get("side", "long"),
                "size": size,
                "entry_price": float(p.get("entryPrice", 0) or 0),
                "pnl": float(p.get("unrealizedPnl", 0) or 0),
                "leverage": float(p.get("leverage", 1) or 1),
                "exchange": exchange_name,
            })
        return positions


# ------------------------------------------------------------------
# 최근 매매 내역
# ------------------------------------------------------------------
async def fetch_recent_trades(
    session: AsyncSession,
    user_id: int,
    exchange_name: str,
    since_days: int = 30,
) -> list[dict[str, Any]]:
    """최근 N일간 매매 내역 조회.

    onboarding._fetch_all_orders와 동일한 거래소별 로직 사용.
    """
    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    since_ms = int(since_dt.timestamp() * 1000)

    async with get_exchange(session, user_id, exchange_name) as ex:
        from src.core.onboarding import _fetch_all_orders

        return await _fetch_all_orders(ex, exchange_name, since_ms)


# ------------------------------------------------------------------
# 펀딩비 조회 (바이낸스 선물만)
# ------------------------------------------------------------------
async def fetch_funding_rates(
    session: AsyncSession,
    user_id: int,
    exchange_name: str,
    symbols: list[str] | None = None,
) -> dict[str, float]:
    """펀딩비 조회 → {symbol: rate}.

    업비트/빗썸은 현물만이므로 빈 dict 반환.
    """
    if exchange_name in ("upbit", "bithumb"):
        return {}

    async with get_exchange(session, user_id, exchange_name) as ex:
        result: dict[str, float] = {}
        target_symbols = symbols or ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        for sym in target_symbols:
            try:
                funding = await ex.fetch_funding_rate(sym)
                rate = funding.get("fundingRate", 0)
                if rate is not None:
                    result[sym] = float(rate)
            except Exception:
                pass
        return result


# ------------------------------------------------------------------
# 김프 (한국 프리미엄) 계산
# ------------------------------------------------------------------
async def calculate_kimchi_premium(
    session: AsyncSession,
    user_id: int,
    symbol: str = "BTC",
) -> float | None:
    """김프(한국 프리미엄) 계산.

    kimchi_premium = upbit_price / (binance_price * usd_krw_rate) - 1
    Returns: 프리미엄 비율 (0.03 = 3%), 계산 불가 시 None.
    """
    usd_krw = await _get_usd_krw_rate()

    krw_symbol = f"{symbol}/KRW"
    usdt_symbol = f"{symbol}/USDT"

    upbit_price: float | None = None
    binance_price: float | None = None

    # 업비트 가격
    conns = await get_user_connections(session, user_id)
    for conn in conns:
        if conn.exchange == "upbit" and upbit_price is None:
            try:
                async with get_exchange(session, user_id, "upbit") as ex:
                    ticker = await ex.fetch_ticker(krw_symbol)
                    upbit_price = ticker.get("last")
            except Exception:
                pass
        if conn.exchange == "binance" and binance_price is None:
            try:
                async with get_exchange(session, user_id, "binance") as ex:
                    ticker = await ex.fetch_ticker(usdt_symbol)
                    binance_price = ticker.get("last")
            except Exception:
                pass

    if upbit_price and binance_price and usd_krw > 0:
        return upbit_price / (binance_price * usd_krw) - 1
    return None
