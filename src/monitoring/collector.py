"""Tier 2 심층 수집 — 시그널 트리거 발동 시 Intelligence 기반 데이터 수집.

수집 순서 (비용 최적화):
① Base 데이터 (비용 0)
② 외부 API: CMC, CryptoPanic (비용 저)
③ Tavily 웹 검색 — 한+영 동시 (비용 중)
④ Playwright 차트 캡처 (비용 높)

①②로 충분하면 ③④ 안 씀.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import CMC_API_KEY, CRYPTOPANIC_API_KEY
from src.db.models import BaseStream, Trade, User, UserTrigger

logger = logging.getLogger(__name__)


async def collect_deep(
    session: AsyncSession,
    user: User,
    trigger: UserTrigger,
) -> dict[str, Any]:
    """시그널 트리거 발동 시 심층 수집.

    Returns:
        {
            "symbol": str,
            "base_data": {...},
            "api_data": {...},
            "search_data": str | None,
            "chart_png": bytes | None,
            "sufficient_at_tier": 1|2|3|4,
        }
    """
    symbol = _extract_symbol(trigger)
    result: dict[str, Any] = {
        "symbol": symbol,
        "base_data": {},
        "api_data": {},
        "search_data": None,
        "chart_png": None,
        "sufficient_at_tier": 1,
    }

    # ① Base 데이터 (비용 0)
    base = await _collect_base(session, user.id, symbol)
    result["base_data"] = base

    # 데이터 충분성 체크 — 가격 + 펀딩비 + OI 있으면 ②까지만
    has_price = bool(base.get("price"))
    has_derivatives = bool(base.get("funding") or base.get("oi"))

    # ② 외부 API (비용 저)
    api_data = await _collect_api(symbol)
    result["api_data"] = api_data
    result["sufficient_at_tier"] = 2

    # ①②로 충분한지 판단
    has_news = bool(api_data.get("news"))
    if has_price and has_derivatives and has_news:
        logger.info("Tier 2 수집 충분: %s (tier 1+2)", symbol)
        return result

    # ③ Tavily 웹 검색 (비용 중)
    search = await _collect_search(symbol, user.language or "ko")
    result["search_data"] = search
    result["sufficient_at_tier"] = 3

    # ④ 차트 캡처 (비용 높) — trigger에 chart_needed 있을 때만
    if trigger.condition and isinstance(trigger.condition, dict):
        if trigger.condition.get("chart_needed"):
            chart = await _capture_chart_safe(symbol)
            if chart:
                result["chart_png"] = chart
                result["sufficient_at_tier"] = 4

    logger.info(
        "심층 수집 완료: %s (tier %d)", symbol, result["sufficient_at_tier"],
    )
    return result


# ------------------------------------------------------------------
# ① Base 데이터
# ------------------------------------------------------------------


async def _collect_base(
    session: AsyncSession,
    user_id: int,
    symbol: str | None,
) -> dict[str, Any]:
    """유저의 Base 스트림에서 관련 데이터 수집."""
    data: dict[str, Any] = {}

    q = select(BaseStream).where(
        BaseStream.user_id == user_id,
        BaseStream.temperature.in_(["hot", "warm"]),
        BaseStream.last_value.isnot(None),
    )
    result = await session.execute(q)
    streams = result.scalars().all()

    for s in streams:
        # 관련 종목 또는 전체 데이터
        if s.symbol is None or (symbol and symbol.upper() in (s.symbol or "").upper()):
            data[s.stream_type] = s.last_value
        # BTC/ETH는 항상 포함 (시장 전체 상황)
        elif s.symbol in ("BTC", "ETH"):
            data[f"{s.stream_type}_{s.symbol}"] = s.last_value

    # 보유 포지션
    pos_result = await session.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.status == "open",
        )
    )
    open_trades = pos_result.scalars().all()
    if open_trades:
        data["positions"] = [
            {
                "symbol": t.symbol,
                "side": t.side,
                "entry_price": t.entry_price,
                "leverage": t.leverage,
            }
            for t in open_trades
        ]

    return data


# ------------------------------------------------------------------
# ② 외부 API
# ------------------------------------------------------------------


async def _collect_api(symbol: str | None) -> dict[str, Any]:
    """CMC + CryptoPanic 데이터 수집."""
    data: dict[str, Any] = {}

    # CryptoPanic 뉴스
    if CRYPTOPANIC_API_KEY and symbol:
        try:
            import aiohttp

            api_url = "https://cryptopanic.com/api/v1/posts/"
            params = {
                "auth_token": CRYPTOPANIC_API_KEY,
                "currencies": symbol,
                "kind": "news",
                "filter": "important",
            }
            async with aiohttp.ClientSession() as client:
                async with client.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        results = body.get("results", [])[:5]
                        data["news"] = [
                            {
                                "title": r.get("title", ""),
                                "url": r.get("url", ""),
                                "created_at": r.get("created_at", ""),
                            }
                            for r in results
                        ]
        except Exception:
            logger.warning("CryptoPanic 수집 실패: %s", symbol, exc_info=True)

    # CMC 데이터
    if CMC_API_KEY and symbol:
        try:
            import aiohttp

            url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
            headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
            params = {"symbol": symbol, "convert": "USD"}
            async with aiohttp.ClientSession() as client:
                async with client.get(
                    url, headers=headers, params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        coin_data = body.get("data", {}).get(symbol, {})
                        quote = coin_data.get("quote", {}).get("USD", {})
                        data["cmc"] = {
                            "price": quote.get("price"),
                            "volume_24h": quote.get("volume_24h"),
                            "change_1h": quote.get("percent_change_1h"),
                            "change_24h": quote.get("percent_change_24h"),
                            "change_7d": quote.get("percent_change_7d"),
                            "market_cap": quote.get("market_cap"),
                            "cmc_rank": coin_data.get("cmc_rank"),
                        }
        except Exception:
            logger.warning("CMC 수집 실패: %s", symbol, exc_info=True)

    return data


# ------------------------------------------------------------------
# ③ Tavily 웹 검색
# ------------------------------------------------------------------


async def _collect_search(symbol: str | None, language: str) -> str | None:
    """Tavily 양방향 검색."""
    if not symbol:
        return None

    try:
        from src.data.search import autonomous_search

        return await autonomous_search(
            query=f"{symbol} crypto analysis latest news",
            user_language=language,
        )
    except Exception:
        logger.warning("Tavily 수집 실패: %s", symbol, exc_info=True)
        return None


# ------------------------------------------------------------------
# ④ 차트 캡처
# ------------------------------------------------------------------


async def _capture_chart_safe(symbol: str | None) -> bytes | None:
    """차트 캡처 (실패 시 None)."""
    if not symbol:
        return None
    try:
        from src.data.chart import capture_chart

        return await capture_chart(symbol=symbol, timeframe="4h")
    except Exception:
        logger.warning("차트 캡처 실패: %s", symbol, exc_info=True)
        return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _extract_symbol(trigger: UserTrigger) -> str | None:
    """트리거에서 종목 심볼 추출."""
    if trigger.condition and isinstance(trigger.condition, dict):
        sym = trigger.condition.get("symbol")
        if sym:
            return sym

    # description에서 심볼 추출 시도
    import re

    desc = trigger.description or ""
    match = re.search(r"\b([A-Z]{2,6})\b", desc)
    if match:
        noise = {"THE", "AND", "FOR", "BUY", "SELL"}
        sym = match.group(1)
        if sym not in noise:
            return sym
    return None
