"""외부 마켓 데이터 수집 — Base 폴링 루프에서 호출.

공개 API만 사용 (유저 거래소 키 불필요):
- 바이낸스 공개 API: 가격, 펀딩비, OI
- CoinMarketCap: Fear & Greed 지수
- CryptoPanic: 뉴스 헤드라인

Base 스트림 타입별 수집 함수 매핑.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import CMC_API_KEY, CRYPTOPANIC_API_KEY

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 10


# ------------------------------------------------------------------
# 메인 디스패처 — stream_type별 데이터 수집
# ------------------------------------------------------------------


async def fetch_stream_data(
    stream_type: str,
    symbol: str | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """스트림 타입에 따라 적절한 데이터 수집.

    Returns:
        수집된 데이터 dict 또는 None (실패 시)
    """
    try:
        if stream_type == "price":
            return await _fetch_price(symbol or "BTC")
        elif stream_type == "funding":
            return await _fetch_funding(symbol or "BTC")
        elif stream_type == "oi":
            return await _fetch_oi(symbol or "BTC")
        elif stream_type == "news":
            return await _fetch_news()
        elif stream_type == "indicator":
            if symbol == "fear_greed":
                return await _fetch_fear_greed()
            return None
        elif stream_type == "spread":
            if symbol == "kimchi":
                return await _fetch_kimchi_spread()
            return None
        else:
            return None
    except Exception:
        logger.debug(
            "스트림 데이터 수집 실패: %s/%s", stream_type, symbol, exc_info=True,
        )
        return None


# ------------------------------------------------------------------
# 바이낸스 공개 API — 가격
# ------------------------------------------------------------------


async def _fetch_price(symbol: str) -> dict[str, Any] | None:
    """바이낸스 공개 API로 가격 데이터 수집."""
    pair = f"{symbol.upper()}USDT"
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={pair}"

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    return {
        "last": float(data.get("lastPrice", 0)),
        "high_24h": float(data.get("highPrice", 0)),
        "low_24h": float(data.get("lowPrice", 0)),
        "volume_24h": float(data.get("quoteVolume", 0)),
        "change_24h_pct": float(data.get("priceChangePercent", 0)),
        "volume_ratio": None,  # 별도 계산 필요
    }


# ------------------------------------------------------------------
# 바이낸스 공개 API — 펀딩비
# ------------------------------------------------------------------


async def _fetch_funding(symbol: str) -> dict[str, Any] | None:
    """바이낸스 선물 펀딩비 조회."""
    pair = f"{symbol.upper()}USDT"
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={pair}&limit=1"

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    if not data:
        return None

    latest = data[0]
    rate = float(latest.get("fundingRate", 0))
    return {
        "rate": rate,
        "rate_pct": rate * 100,
        "time": latest.get("fundingTime"),
    }


# ------------------------------------------------------------------
# 바이낸스 공개 API — OI (미결제약정)
# ------------------------------------------------------------------


async def _fetch_oi(symbol: str) -> dict[str, Any] | None:
    """바이낸스 선물 OI 조회."""
    pair = f"{symbol.upper()}USDT"
    url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={pair}"

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    oi = float(data.get("openInterest", 0))
    return {
        "open_interest": oi,
        "symbol": pair,
        "change_pct": None,  # 이전 값과 비교 필요
    }


# ------------------------------------------------------------------
# CryptoPanic — 뉴스
# ------------------------------------------------------------------


async def _fetch_news() -> dict[str, Any] | None:
    """CryptoPanic API로 최신 뉴스 헤드라인 수집."""
    if not CRYPTOPANIC_API_KEY:
        return {"headlines": [], "source": "cryptopanic", "error": "API key not set"}

    api_url = "https://cryptopanic.com/api/free/v1/posts/"
    params = {
        "auth_token": CRYPTOPANIC_API_KEY,
        "kind": "news",
        "filter": "hot",
        "public": "true",
    }

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(api_url, params=params)
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    headlines = [r.get("title", "") for r in results[:10]]
    return {
        "headlines": headlines,
        "count": len(headlines),
        "source": "cryptopanic",
    }


# ------------------------------------------------------------------
# Fear & Greed Index
# ------------------------------------------------------------------


async def _fetch_fear_greed() -> dict[str, Any] | None:
    """Alternative.me Fear & Greed Index 조회."""
    url = "https://api.alternative.me/fng/?limit=1"

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    fng_data = data.get("data", [{}])[0]
    return {
        "value": int(fng_data.get("value", 50)),
        "classification": fng_data.get("value_classification", "Neutral"),
        "timestamp": fng_data.get("timestamp"),
    }


# ------------------------------------------------------------------
# 김프 (한국 프리미엄) — 공개 API
# ------------------------------------------------------------------


async def _fetch_kimchi_spread() -> dict[str, Any] | None:
    """업비트 vs 바이낸스 BTC 가격 비교 → 김프 계산.

    공개 API만 사용 (유저 키 불필요).
    """
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        # 업비트 BTC/KRW
        upbit_resp = await client.get(
            "https://api.upbit.com/v1/ticker?markets=KRW-BTC"
        )
        upbit_resp.raise_for_status()
        upbit_data = upbit_resp.json()
        upbit_price = float(upbit_data[0].get("trade_price", 0))

        # 바이낸스 BTC/USDT
        binance_resp = await client.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
        )
        binance_resp.raise_for_status()
        binance_data = binance_resp.json()
        binance_price = float(binance_data.get("price", 0))

        # USD/KRW 환율
        rate_resp = await client.get(
            "https://api.exchangerate-api.com/v4/latest/USD"
        )
        rate_resp.raise_for_status()
        rate_data = rate_resp.json()
        usd_krw = float(rate_data.get("rates", {}).get("KRW", 1350))

    if upbit_price > 0 and binance_price > 0 and usd_krw > 0:
        premium = upbit_price / (binance_price * usd_krw) - 1
        return {
            "premium_pct": round(premium * 100, 2),
            "upbit_btc_krw": upbit_price,
            "binance_btc_usd": binance_price,
            "usd_krw": usd_krw,
        }
    return None
