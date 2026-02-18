"""Tier 1 Base — 실시간 데이터 스트림 + 온도 관리.

AI 미사용. 비용 0 (Hot만 실시간 API 비용).

온도 관리:
  Hot  (7일 내 언급): 10초 폴링, Redis 캐싱
  Warm (7~30일):      30분 폴링
  Cold (30일+):       Patrol에서만 체크

절대 삭제 안 함. 재언급 시 Cold→Hot 즉시 복원.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import (
    HOT_THRESHOLD_DAYS,
    REDIS_URL,
    WARM_THRESHOLD_DAYS,
)
from src.db.models import BaseStream, User

logger = logging.getLogger(__name__)

# Redis 캐시 TTL (초)
_REDIS_HOT_TTL = 60

# In-memory fallback (Redis 미연결 시)
_mem_cache: dict[str, tuple[Any, float]] = {}
_MEM_TTL = 60.0


# ------------------------------------------------------------------
# Redis 연결 (lazy)
# ------------------------------------------------------------------
_redis_client: Any = None
_redis_attempted = False


async def _get_redis() -> Any:
    """Redis 클라이언트 반환. 미연결이면 None (in-memory fallback)."""
    global _redis_client, _redis_attempted
    if _redis_client is not None:
        return _redis_client
    if _redis_attempted:
        return None
    _redis_attempted = True

    if not REDIS_URL:
        logger.info("REDIS_URL 미설정 — in-memory 캐시 사용")
        return None

    try:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        await _redis_client.ping()
        logger.info("Redis 연결 성공")
        return _redis_client
    except Exception:
        logger.warning("Redis 연결 실패 — in-memory 캐시 사용", exc_info=True)
        _redis_client = None
        return None


# ------------------------------------------------------------------
# 캐시 추상화 (Redis / in-memory)
# ------------------------------------------------------------------


async def _cache_set(key: str, value: Any, ttl: int = _REDIS_HOT_TTL) -> None:
    """캐시에 값 저장."""
    r = await _get_redis()
    val_str = json.dumps(value, ensure_ascii=False, default=str)
    if r:
        try:
            await r.set(key, val_str, ex=ttl)
            return
        except Exception:
            pass
    _mem_cache[key] = (val_str, time.time() + ttl)


async def _cache_get(key: str) -> Any | None:
    """캐시에서 값 조회."""
    r = await _get_redis()
    if r:
        try:
            raw = await r.get(key)
            if raw:
                return json.loads(raw)
            return None
        except Exception:
            pass
    entry = _mem_cache.get(key)
    if entry:
        val_str, expires_at = entry
        if time.time() < expires_at:
            return json.loads(val_str)
        del _mem_cache[key]
    return None


def _cache_key(user_id: int, stream_type: str, symbol: str | None) -> str:
    """Base 캐시 키 생성."""
    sym = symbol or "all"
    return f"base:{user_id}:{stream_type}:{sym}"


# ------------------------------------------------------------------
# BaseManager
# ------------------------------------------------------------------


class BaseManager:
    """Tier 1 Base 데이터 스트림 관리자."""

    # ---- Default Preset 생성 (온보딩 완료 시) ----

    @staticmethod
    async def create_default_preset(
        session: AsyncSession,
        user: User,
    ) -> int:
        """온보딩 완료 시 기본 프리셋 생성.

        Returns:
            생성된 스트림 수
        """
        presets: list[dict[str, Any]] = [
            # 시세
            {"stream_type": "price", "symbol": "BTC", "config": {"source": "binance"}},
            {"stream_type": "price", "symbol": "ETH", "config": {"source": "binance"}},
            # 파생
            {"stream_type": "funding", "symbol": "BTC", "config": {"source": "binance"}},
            {"stream_type": "funding", "symbol": "ETH", "config": {"source": "binance"}},
            {"stream_type": "oi", "symbol": "BTC", "config": {"source": "binance"}},
            {"stream_type": "oi", "symbol": "ETH", "config": {"source": "binance"}},
            # 뉴스
            {"stream_type": "news", "symbol": None, "config": {"source": "cryptopanic"}},
            # 지표
            {"stream_type": "indicator", "symbol": "fear_greed", "config": {"source": "cmc"}},
            {"stream_type": "spread", "symbol": "kimchi", "config": {"source": "upbit_binance"}},
        ]

        count = 0
        for p in presets:
            # 중복 체크
            existing = await session.execute(
                select(BaseStream).where(
                    BaseStream.user_id == user.id,
                    BaseStream.stream_type == p["stream_type"],
                    BaseStream.symbol == p["symbol"],
                )
            )
            if existing.scalar_one_or_none():
                continue

            session.add(BaseStream(
                user_id=user.id,
                stream_type=p["stream_type"],
                symbol=p["symbol"],
                config=p["config"],
                temperature="hot",
            ))
            count += 1

        if count:
            await session.flush()
            logger.info("Base 프리셋 생성: user=%s, %d개", user.telegram_id, count)
        return count

    # ---- 온도 관리 ----

    @staticmethod
    async def update_temperature(
        session: AsyncSession,
        user_id: int,
        symbol: str,
    ) -> None:
        """종목 언급 시 해당 스트림을 Hot으로 전환 + last_mentioned_at 갱신."""
        now = datetime.now(timezone.utc)
        result = await session.execute(
            select(BaseStream).where(
                BaseStream.user_id == user_id,
                BaseStream.symbol == symbol,
            )
        )
        streams = result.scalars().all()

        for stream in streams:
            changed = stream.temperature != "hot"
            stream.temperature = "hot"
            stream.last_mentioned_at = now
            if changed:
                logger.info(
                    "온도 전환: %s/%s → Hot (user_id=%d)",
                    stream.stream_type, stream.symbol, user_id,
                )
        if streams:
            await session.flush()

    @staticmethod
    async def auto_transition_temperatures(
        session: AsyncSession,
        user_id: int,
    ) -> dict[str, int]:
        """시간 기반 온도 자동 전환. Patrol에서 호출.

        Returns:
            {"hot_to_warm": N, "warm_to_cold": N}
        """
        now = datetime.now(timezone.utc)
        hot_cutoff = now - timedelta(days=HOT_THRESHOLD_DAYS)
        warm_cutoff = now - timedelta(days=WARM_THRESHOLD_DAYS)

        changes = {"hot_to_warm": 0, "warm_to_cold": 0}

        # Hot → Warm (7일 미언급)
        result = await session.execute(
            select(BaseStream).where(
                BaseStream.user_id == user_id,
                BaseStream.temperature == "hot",
                BaseStream.last_mentioned_at < hot_cutoff,
            )
        )
        for stream in result.scalars().all():
            stream.temperature = "warm"
            changes["hot_to_warm"] += 1
            logger.debug("Hot→Warm: %s/%s", stream.stream_type, stream.symbol)

        # Warm → Cold (30일 미언급)
        result = await session.execute(
            select(BaseStream).where(
                BaseStream.user_id == user_id,
                BaseStream.temperature == "warm",
                BaseStream.last_mentioned_at < warm_cutoff,
            )
        )
        for stream in result.scalars().all():
            stream.temperature = "cold"
            changes["warm_to_cold"] += 1
            logger.debug("Warm→Cold: %s/%s", stream.stream_type, stream.symbol)

        if changes["hot_to_warm"] or changes["warm_to_cold"]:
            await session.flush()
            logger.info(
                "온도 전환: user_id=%d, hot→warm=%d, warm→cold=%d",
                user_id, changes["hot_to_warm"], changes["warm_to_cold"],
            )
        return changes

    # ---- Hot 데이터 조회 ----

    @staticmethod
    async def get_hot_data(
        session: AsyncSession,
        user_id: int,
    ) -> dict[str, Any]:
        """유저의 Hot 스트림 데이터를 Redis/캐시에서 조회.

        Returns:
            {"{stream_type}/{symbol}": value, ...}
        """
        result = await session.execute(
            select(BaseStream).where(
                BaseStream.user_id == user_id,
                BaseStream.temperature == "hot",
            )
        )
        hot_streams = result.scalars().all()

        data: dict[str, Any] = {}
        for s in hot_streams:
            key = _cache_key(user_id, s.stream_type, s.symbol)
            cached = await _cache_get(key)
            if cached is not None:
                data[f"{s.stream_type}/{s.symbol or 'all'}"] = cached
            elif s.last_value:
                data[f"{s.stream_type}/{s.symbol or 'all'}"] = s.last_value
        return data

    # ---- 데이터 업데이트 ----

    @staticmethod
    async def update_stream_value(
        session: AsyncSession,
        stream: BaseStream,
        value: Any,
    ) -> None:
        """스트림 값 업데이트 — Redis 캐싱 + DB last_value."""
        stream.last_value = value
        await session.flush()

        # Redis 캐싱 (Hot만)
        if stream.temperature == "hot":
            key = _cache_key(stream.user_id, stream.stream_type, stream.symbol)
            await _cache_set(key, value)

    # ---- Hot/Warm 폴링 대상 조회 ----

    @staticmethod
    async def get_streams_to_poll(
        session: AsyncSession,
        temperature: str,
    ) -> list[BaseStream]:
        """특정 온도의 모든 스트림 조회 (폴링 대상)."""
        result = await session.execute(
            select(BaseStream).where(
                BaseStream.temperature == temperature,
            )
        )
        return list(result.scalars().all())

    # ---- 스트림 추가 ----

    @staticmethod
    async def add_stream(
        session: AsyncSession,
        user_id: int,
        stream_type: str,
        symbol: str | None,
        config: dict[str, Any] | None = None,
        temperature: str = "hot",
    ) -> BaseStream:
        """Base 스트림 추가 (중복 시 기존 반환 + Hot 전환)."""
        result = await session.execute(
            select(BaseStream).where(
                BaseStream.user_id == user_id,
                BaseStream.stream_type == stream_type,
                BaseStream.symbol == symbol,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # 이미 존재 → Hot으로 복원
            existing.temperature = temperature
            existing.last_mentioned_at = datetime.now(timezone.utc)
            if config:
                existing.config = config
            await session.flush()
            return existing

        stream = BaseStream(
            user_id=user_id,
            stream_type=stream_type,
            symbol=symbol,
            config=config or {},
            temperature=temperature,
        )
        session.add(stream)
        await session.flush()
        return stream
