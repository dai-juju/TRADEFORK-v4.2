"""TRADEFORK — FastAPI + Telegram Bot 앱 진입점.

Phase 10: 전체 통합 + 스케줄러 + 배포 준비.

Startup 순서:
1. DB 테이블 생성
2. Redis 연결 (lazy — Base 첫 호출 시)
3. Pinecone 연결 확인
4. Telegram 봇 polling 시작
5. APScheduler 시작:
   - 매매 감지: 30초
   - Base 폴링: Hot 10초, Warm 30분
   - Patrol 순찰: 1시간
   - Base 온도 관리: 1시간
   - 시그널 카운트 리셋: 매일 00:00 UTC
   - LLM 트리거 자동 정리: 1시간 (72h 무반응 삭제)

Shutdown 순서:
1. APScheduler 종료
2. Base 폴링 중단
3. 매매 감지 중단
4. Telegram 봇 종료
5. DB 엔진 dispose
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from sqlalchemy import select
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.bot.handlers import (
    callback_handler,
    dailybrief_command,
    demo_start_command,
    help_command,
    message_handler,
    photo_handler,
    principles_command,
    start_command,
    sync_command,
)
from src.config import (
    HOT_POLL_INTERVAL,
    PRO_PATROL_INTERVAL_SECONDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_DEMO_BOT_TOKEN,
    TRADE_POLL_INTERVAL,
    WARM_POLL_INTERVAL,
)
from src.db.migrations import create_tables

logger = logging.getLogger(__name__)

# Module-level — shutdown 접근용
_tg_app: Application | None = None
_tg_demo_app: Application | None = None
_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — startup / shutdown."""
    global _tg_app, _tg_demo_app, _scheduler

    # ============================================================
    # STARTUP
    # ============================================================
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("TRADEFORK 시작...")

    # 1) DB 테이블 생성
    await create_tables()
    logger.info("DB 테이블 준비 완료.")

    # 2) Redis 연결 확인 (lazy — Base 첫 호출 시 실제 연결)
    try:
        from src.monitoring.base import _get_redis
        await _get_redis()
    except Exception:
        logger.warning("Redis 사전 연결 실패 — in-memory fallback 사용")

    # 3) Pinecone 연결 확인 (lazy — 첫 임베딩 시 실제 연결)
    try:
        from src.config import PINECONE_API_KEY
        if PINECONE_API_KEY:
            logger.info("Pinecone API 키 확인됨 (lazy init)")
        else:
            logger.warning("PINECONE_API_KEY 미설정")
    except Exception:
        logger.warning("Pinecone 설정 확인 실패")

    # 4) Telegram Bot
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다!")
    else:
        _tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Handler 등록 (순서 중요: 명령어 → 콜백 → 사진 → 일반 메시지)
        _tg_app.add_handler(CommandHandler("start", start_command))
        _tg_app.add_handler(CommandHandler("help", help_command))
        _tg_app.add_handler(CommandHandler("sync", sync_command))
        _tg_app.add_handler(CommandHandler("principles", principles_command))
        _tg_app.add_handler(CommandHandler("dailybrief", dailybrief_command))
        _tg_app.add_handler(CallbackQueryHandler(callback_handler))
        _tg_app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
        _tg_app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
        )

        await _tg_app.initialize()
        await _tg_app.start()
        await _tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram 메인 봇 polling 시작.")

    # 4-b) Demo Bot (학습된 FORKER 체험용)
    if TELEGRAM_DEMO_BOT_TOKEN:
        _tg_demo_app = Application.builder().token(TELEGRAM_DEMO_BOT_TOKEN).build()

        _tg_demo_app.add_handler(CommandHandler("start", demo_start_command))
        _tg_demo_app.add_handler(CommandHandler("help", help_command))
        _tg_demo_app.add_handler(CommandHandler("sync", sync_command))
        _tg_demo_app.add_handler(CommandHandler("principles", principles_command))
        _tg_demo_app.add_handler(CommandHandler("dailybrief", dailybrief_command))
        _tg_demo_app.add_handler(CallbackQueryHandler(callback_handler))
        _tg_demo_app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
        _tg_demo_app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
        )

        await _tg_demo_app.initialize()
        await _tg_demo_app.start()
        await _tg_demo_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram 데모 봇 polling 시작.")

    # 5) 백그라운드 태스크 + APScheduler
    trade_task: asyncio.Task | None = None
    base_task: asyncio.Task | None = None

    if _tg_app:
        # 매매 감지 폴링 (30초)
        trade_task = asyncio.create_task(_trade_poll_loop(_tg_app.bot))
        logger.info("매매 감지 폴링 시작 (주기: %ds)", TRADE_POLL_INTERVAL)

        # Base 데이터 폴링 (Hot 10초, Warm 30분)
        base_task = asyncio.create_task(_base_poll_loop(_tg_app.bot))
        logger.info("Base 데이터 폴링 시작 (Hot: %ds, Warm: %ds)", HOT_POLL_INTERVAL, WARM_POLL_INTERVAL)

        # APScheduler
        _scheduler = AsyncIOScheduler()

        # Patrol 순찰 (1시간)
        _scheduler.add_job(
            _patrol_scheduled_job,
            "interval",
            seconds=PRO_PATROL_INTERVAL_SECONDS,
            id="patrol_job",
            name="FORKER Patrol",
            misfire_grace_time=300,
        )

        # Base 온도 관리 (1시간)
        _scheduler.add_job(
            _temperature_management_job,
            "interval",
            seconds=3600,
            id="temp_mgmt_job",
            name="Base 온도 관리",
            misfire_grace_time=300,
        )

        # 시그널 카운트 리셋 (매일 00:00 UTC)
        _scheduler.add_job(
            _daily_signal_reset_job,
            "cron",
            hour=0,
            minute=0,
            timezone="UTC",
            id="signal_reset_job",
            name="시그널 카운트 리셋",
            misfire_grace_time=600,
        )

        # LLM 트리거 자동 정리 (1시간마다 — 72h 무반응 삭제)
        _scheduler.add_job(
            _trigger_cleanup_job,
            "interval",
            seconds=3600,
            id="trigger_cleanup_job",
            name="트리거 자동 정리",
            misfire_grace_time=300,
        )

        # 데일리 브리핑 (5분마다 체크 → KST 정시에 전송)
        _scheduler.add_job(
            _daily_briefing_job,
            "interval",
            minutes=5,
            id="daily_briefing_job",
            name="데일리 브리핑",
            misfire_grace_time=300,
        )

        _scheduler.start()
        logger.info(
            "APScheduler 시작: Patrol(%ds), 온도관리(1h), 시그널리셋(00:00 UTC), 트리거정리(1h), 브리핑(5min)",
            PRO_PATROL_INTERVAL_SECONDS,
        )

    logger.info("=== TRADEFORK 시작 완료 ===")

    yield

    # ============================================================
    # SHUTDOWN
    # ============================================================
    logger.info("TRADEFORK 종료 중...")

    # 1) APScheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler 종료.")

    # 2) Base 폴링
    if base_task:
        base_task.cancel()
        try:
            await base_task
        except asyncio.CancelledError:
            pass
        logger.info("Base 데이터 폴링 종료.")

    # 3) 매매 감지
    if trade_task:
        trade_task.cancel()
        try:
            await trade_task
        except asyncio.CancelledError:
            pass
        logger.info("매매 감지 폴링 종료.")

    # 4) Telegram Bots
    if _tg_demo_app:
        logger.info("Telegram 데모 봇 종료 중...")
        await _tg_demo_app.updater.stop()
        await _tg_demo_app.stop()
        await _tg_demo_app.shutdown()
        logger.info("Telegram 데모 봇 종료 완료.")

    if _tg_app:
        logger.info("Telegram 메인 봇 종료 중...")
        await _tg_app.updater.stop()
        await _tg_app.stop()
        await _tg_app.shutdown()
        logger.info("Telegram 메인 봇 종료 완료.")

    # 5) DB 엔진 dispose
    try:
        from src.db.session import engine
        await engine.dispose()
        logger.info("DB 엔진 종료.")
    except Exception:
        pass

    logger.info("=== TRADEFORK 종료 완료 ===")


# ==================================================================
# 매매 감지 루프 (30초 주기)
# ==================================================================


async def _trade_poll_loop(bot) -> None:
    """매매 감지 + 포지션 모니터링 백그라운드 루프."""
    from src.db.models import User
    from src.db.session import async_session_factory
    from src.exchange.position_tracker import monitor_positions
    from src.exchange.trade_detector import detect_closed_trades, poll_trades

    await asyncio.sleep(10)  # 초기 대기

    while True:
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(User).where(
                        User.onboarding_step >= 4,
                        User.is_active.is_(True),
                    )
                )
                users = result.scalars().all()

                for user in users:
                    try:
                        detected = await poll_trades(session, user, bot)
                        if detected:
                            logger.info(
                                "매매 감지: user=%s, %d건", user.telegram_id, detected
                            )
                        await detect_closed_trades(session, user, bot)
                        await monitor_positions(session, user, bot)
                    except Exception:
                        logger.error(
                            "유저 매매 감지 실패: %s", user.telegram_id, exc_info=True
                        )
        except Exception:
            logger.error("매매 감지 루프 에러", exc_info=True)

        await asyncio.sleep(TRADE_POLL_INTERVAL)


# ==================================================================
# Base 데이터 폴링 루프 (Hot: 10초, Warm: 30분)
# ==================================================================


async def _base_poll_loop(bot) -> None:
    """Base 데이터 폴링 + 트리거 매칭 루프.

    매 Hot 사이클마다:
    1. Hot 스트림 데이터 수집 (공개 API)
    2. 트리거 매칭

    매 Warm 사이클마다 (30분):
    3. Warm 스트림 데이터 수집
    """
    from src.data.market import fetch_stream_data
    from src.db.models import User
    from src.db.session import async_session_factory
    from src.monitoring.base import BaseManager
    from src.monitoring.trigger import TriggerManager

    await asyncio.sleep(15)  # 초기 대기 (매매 감지보다 약간 늦게)

    hot_cycle = 0
    warm_interval = max(WARM_POLL_INTERVAL // HOT_POLL_INTERVAL, 1)

    while True:
        try:
            hot_cycle += 1
            is_warm_cycle = hot_cycle % warm_interval == 0

            async with async_session_factory() as session:
                # Hot 스트림 데이터 수집
                hot_streams = await BaseManager.get_streams_to_poll(session, "hot")
                for stream in hot_streams:
                    try:
                        data = await fetch_stream_data(
                            stream.stream_type, stream.symbol, stream.config,
                        )
                        if data:
                            await BaseManager.update_stream_value(session, stream, data)
                    except Exception:
                        logger.debug(
                            "Hot 스트림 수집 실패: %s/%s",
                            stream.stream_type, stream.symbol,
                        )

                # Warm 사이클
                if is_warm_cycle:
                    warm_streams = await BaseManager.get_streams_to_poll(session, "warm")
                    for stream in warm_streams:
                        try:
                            data = await fetch_stream_data(
                                stream.stream_type, stream.symbol, stream.config,
                            )
                            if data:
                                await BaseManager.update_stream_value(
                                    session, stream, data,
                                )
                        except Exception:
                            logger.debug(
                                "Warm 스트림 수집 실패: %s/%s",
                                stream.stream_type, stream.symbol,
                            )

                await session.commit()

                # 트리거 매칭 (유저별)
                result = await session.execute(
                    select(User).where(
                        User.onboarding_step >= 4,
                        User.is_active.is_(True),
                    )
                )
                users = result.scalars().all()

                for user in users:
                    try:
                        hot_data = await BaseManager.get_hot_data(session, user.id)
                        if hot_data:
                            fired = await TriggerManager.evaluate_all(
                                session,
                                user_id=user.id,
                                current_data=hot_data,
                                bot=bot,
                                telegram_id=user.telegram_id,
                            )
                            if fired:
                                logger.info(
                                    "트리거 발동: user=%s, %d건",
                                    user.telegram_id, fired,
                                )
                    except Exception:
                        logger.error(
                            "Base 트리거 매칭 실패: %s",
                            user.telegram_id, exc_info=True,
                        )

        except Exception:
            logger.error("Base 폴링 루프 에러", exc_info=True)

        await asyncio.sleep(HOT_POLL_INTERVAL)


# ==================================================================
# APScheduler 작업들
# ==================================================================


async def _patrol_scheduled_job() -> None:
    """Patrol 순찰 (1시간)."""
    if not _tg_app:
        return

    from src.monitoring.patrol import patrol_job

    try:
        await patrol_job(_tg_app.bot)
    except Exception:
        logger.error("Patrol 스케줄 작업 실패", exc_info=True)


async def _temperature_management_job() -> None:
    """Base 온도 자동 전환 (1시간).

    Patrol과 별도로 실행 — Patrol이 비활성 유저를 스킵해도
    온도 관리는 항상 실행.
    """
    from src.db.models import User
    from src.db.session import async_session_factory
    from src.monitoring.base import BaseManager

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(User).where(User.is_active.is_(True))
            )
            users = result.scalars().all()

            total_changes = {"hot_to_warm": 0, "warm_to_cold": 0}
            for user in users:
                changes = await BaseManager.auto_transition_temperatures(
                    session, user.id,
                )
                total_changes["hot_to_warm"] += changes.get("hot_to_warm", 0)
                total_changes["warm_to_cold"] += changes.get("warm_to_cold", 0)

            await session.commit()

            if total_changes["hot_to_warm"] or total_changes["warm_to_cold"]:
                logger.info(
                    "온도 관리 완료: hot→warm=%d, warm→cold=%d",
                    total_changes["hot_to_warm"],
                    total_changes["warm_to_cold"],
                )
    except Exception:
        logger.error("온도 관리 작업 실패", exc_info=True)


async def _daily_signal_reset_job() -> None:
    """시그널 카운트 리셋 (매일 00:00 UTC)."""
    from sqlalchemy import update as sa_update

    from src.db.models import User
    from src.db.session import async_session_factory

    try:
        async with async_session_factory() as session:
            await session.execute(
                sa_update(User).where(
                    User.is_active.is_(True),
                ).values(
                    daily_signal_count=0,
                    daily_signal_reset_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            logger.info("시그널 카운트 리셋 완료 (00:00 UTC)")
    except Exception:
        logger.error("시그널 카운트 리셋 실패", exc_info=True)


async def _trigger_cleanup_job() -> None:
    """72시간 무반응 자동 생성 트리거 삭제.

    source='llm_auto' 또는 'patrol'인 트리거 중 72시간 경과 + 미발동 → 비활성화.
    user_request는 유저가 직접 요청한 것이므로 자동 삭제하지 않음.
    """
    from src.db.models import UserTrigger
    from src.db.session import async_session_factory

    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(UserTrigger).where(
                    UserTrigger.source.in_(["llm_auto", "patrol"]),
                    UserTrigger.is_active.is_(True),
                    UserTrigger.triggered_at.is_(None),
                    UserTrigger.created_at < cutoff,
                )
            )
            stale_triggers = result.scalars().all()

            for trigger in stale_triggers:
                trigger.is_active = False

            if stale_triggers:
                await session.commit()
                logger.info(
                    "LLM 트리거 자동 정리: %d건 비활성화", len(stale_triggers),
                )
    except Exception:
        logger.error("트리거 자동 정리 실패", exc_info=True)


async def _daily_briefing_job() -> None:
    """데일리 브리핑 — 매 5분 실행, 현재 KST 시각 정시 유저에게 전송."""
    if not _tg_app:
        return

    from src.core.briefing import generate_and_send_briefing
    from src.db.models import User
    from src.db.session import async_session_factory

    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst)
    current_hour = now_kst.hour
    current_minute = now_kst.minute

    # 정시 0~4분에만 실행 (5분 간격 스케줄러 → 1시간에 1번만 트리거)
    if current_minute > 4:
        return

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(User).where(
                    User.is_active.is_(True),
                    User.onboarding_step >= 4,
                    User.briefing_hour == current_hour,
                )
            )
            users = result.scalars().all()

            for user in users:
                try:
                    await generate_and_send_briefing(session, user, _tg_app.bot)
                except Exception:
                    logger.error(
                        "데일리 브리핑 유저 실패: %s",
                        user.telegram_id, exc_info=True,
                    )

            await session.commit()

    except Exception:
        logger.error("데일리 브리핑 작업 실패", exc_info=True)


# ==================================================================
# FastAPI App
# ==================================================================

app = FastAPI(
    title="TRADEFORK",
    version="1.0.0",
    description="암호화폐 투자 분신 FORKER",
    lifespan=lifespan,
)


# ==================================================================
# Demo 엔드포인트 — 데모 영상 촬영용 수동 트리거
# ==================================================================


@app.post("/demo/signal")
async def demo_signal(telegram_id: int, symbol: str = "SOL") -> dict:
    """데모 시그널 전송 — 하드코딩된 3축 확신도 시그널."""
    if not _tg_app:
        return {"status": "error", "detail": "bot not running"}

    from src.bot.keyboards import signal_feedback
    from src.db.models import ChatMessage, Signal, User
    from src.db.session import async_session_factory
    from src.monitoring.judge import _format_signal_message

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return {"status": "error", "detail": "user not found"}

        parsed = {
            "signal_type": "trade_signal",
            "direction": "long",
            "reasoning": (
                f"{symbol}/USDT 4시간봉 기준으로 봤을 때, 하락 채널 하단 지지선에서 "
                "강한 반등 캔들이 나왔어. 거래량도 전일 대비 240% 급증했고, "
                "이전에 비슷한 패턴에서 롱 진입했을 때 수익률이 좋았던 걸 기억해.\n\n"
                "RSI(14) 38에서 반등 중이고, MACD 히스토그램이 감소세 둔화. "
                "바이낸스 펀딩비도 -0.01%로 숏 과열 상태라 반등 여건이 좋아.\n\n"
                "너의 평소 패턴대로라면 여기서 1차 진입 후, "
                "지지선 재확인 시 2차 추가 매수하는 전략이 맞을 거야."
            ),
            "counter_argument": (
                "글로벌 매크로 불확실성(FOMC 의사록)이 남아있고, "
                "BTC 도미넌스 상승 시 알트 약세 전환 가능성. "
                "직전 지지선 이탈 시 추가 하락 -8% 가능."
            ),
            "confidence": 0.72,
            "confidence_style": 0.82,
            "confidence_history": 0.60,
            "confidence_market": 0.75,
            "stop_loss": f"{symbol} $142.50 (-4.2%)",
        }

        signal = Signal(
            user_id=user.id,
            signal_type=parsed["signal_type"],
            content=parsed["reasoning"],
            reasoning=parsed["reasoning"],
            counter_argument=parsed["counter_argument"],
            confidence=parsed["confidence"],
            confidence_style=parsed["confidence_style"],
            confidence_history=parsed["confidence_history"],
            confidence_market=parsed["confidence_market"],
            symbol=symbol,
            direction=parsed["direction"],
            stop_loss=parsed["stop_loss"],
        )
        session.add(signal)
        await session.flush()

        text = _format_signal_message(parsed, symbol)

        session.add(ChatMessage(
            user_id=user.id,
            role="assistant",
            content=text,
            message_type="text",
            intent="signal_trigger",
            metadata_={"signal_id": signal.id, "demo": True},
        ))
        await session.commit()

        await _tg_app.bot.send_message(
            chat_id=telegram_id,
            text=text,
            reply_markup=signal_feedback(),
        )

    return {"status": "ok", "signal_id": signal.id, "symbol": symbol}


@app.post("/demo/briefing")
async def demo_briefing(telegram_id: int) -> dict:
    """데모 자율 브리핑 — Patrol이 발견한 인사이트 스타일."""
    if not _tg_app:
        return {"status": "error", "detail": "bot not running"}

    from src.db.models import ChatMessage, User
    from src.db.session import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return {"status": "error", "detail": "user not found"}

        text = (
            "🔍 FORKER 자율 순찰 브리핑\n"
            "\n"
            "순찰 돌다가 눈에 띄는 거 발견했어.\n"
            "\n"
            "📊 ETH/BTC 비율이 0.052로 3개월 저점이야. "
            "지난번에도 이 레벨에서 ETH가 반등했었는데, "
            "그때 너 ETH 롱 진입해서 +8.3% 먹었잖아.\n"
            "\n"
            "🔗 온체인 데이터도 흥미로워:\n"
            "  · 고래 지갑 3개가 지난 4시간 동안 ETH 12,400개 매집\n"
            "  · 거래소 ETH 잔고 30일 최저 (매도 압력 감소)\n"
            "  · 스테이킹 TVL +2.3% (7d)\n"
            "\n"
            "⚡ 네가 설정한 'ETH 변동성 확대' 트리거도 근접 중이야.\n"
            "Bollinger Band 수축이 꽤 심해서 조만간 큰 움직임이 나올 수 있어.\n"
            "\n"
            "지금 당장 뭔가 할 필요는 없지만, ETH 쪽 눈여겨봐.\n"
            "궁금한 거 있으면 물어봐!\n"
            "\n"
            "⚠️ TRADEFORK는 매매를 대행하지 않습니다. 최종 판단은 본인의 몫입니다."
        )

        session.add(ChatMessage(
            user_id=user.id,
            role="assistant",
            content=text,
            message_type="text",
            intent="general",
            metadata_={"type": "patrol_briefing", "demo": True},
        ))
        await session.commit()

        await _tg_app.bot.send_message(chat_id=telegram_id, text=text)

    return {"status": "ok"}


@app.post("/demo/daily")
async def demo_daily(telegram_id: int) -> dict:
    """데모 데일리 브리핑 — 실제 generate_and_send_briefing 시도 후 fallback."""
    if not _tg_app:
        return {"status": "error", "detail": "bot not running"}

    from src.db.models import ChatMessage, User
    from src.db.session import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return {"status": "error", "detail": "user not found"}

        # 실제 브리핑 생성 시도
        try:
            from src.core.briefing import generate_and_send_briefing

            await generate_and_send_briefing(session, user, _tg_app.bot)
            await session.commit()
            return {"status": "ok", "source": "live"}
        except Exception:
            logger.warning("데모 데일리: 실제 브리핑 실패, fallback 사용", exc_info=True)

        # Fallback — 하드코딩 브리핑
        text = (
            "📰 데일리 브리핑\n"
            "\n"
            "📈 시장 개요\n"
            "  BTC $97,420 (+1.8%) Vol $38.2B\n"
            "  ETH $3,285 (+2.4%)\n"
            "  Fear&Greed: 62 (Greed)\n"
            "  BTC 펀딩비: 0.012%\n"
            "  김프: +1.85%\n"
            "\n"
            "💼 보유 포지션\n"
            "  SOL/USDT long @ $148.20 (x3)\n"
            "  ETH/USDT long @ $3,150 (x2)\n"
            "  (평균 익절 +12.3% / 손절 -4.8%)\n"
            "\n"
            "📰 주요 뉴스\n"
            "  · SEC, 이더리움 ETF 스테이킹 허용 검토 착수\n"
            "  · 마이크로스트래티지 BTC 2,500개 추가 매수\n"
            "  · 바이낸스 한국 시장 재진출 파트너십 논의 중\n"
            "  · Solana DeFi TVL 사상 최고 $12.8B 돌파\n"
            "\n"
            "🔔 활성 알림\n"
            "  · BTC $100K 돌파 시 알림 (현재 $97,420, -2.6%)\n"
            "  · SOL 거래량 급증 시 알림\n"
            "\n"
            "💬 FORKER:\n"
            "전반적으로 시장 분위기 나쁘지 않아. BTC가 $97K 위에서 "
            "잘 버티고 있고, 알트도 따라 올라오는 중이야. "
            "너 SOL 포지션 현재 +5.2%인데, 평소 패턴대로면 "
            "+10% 근처에서 1차 익절하는 편이니까 조금 더 지켜봐도 좋겠어. "
            "다만 오늘 밤 FOMC 의사록 공개 있으니까 변동성 주의!\n"
            "\n"
            "⚠️ TRADEFORK는 매매를 대행하지 않습니다. 최종 판단은 본인의 몫입니다."
        )

        session.add(ChatMessage(
            user_id=user.id,
            role="assistant",
            content=text,
            message_type="text",
            intent="general",
            metadata_={"type": "daily_briefing", "demo": True},
        ))
        await session.commit()

        await _tg_app.bot.send_message(chat_id=telegram_id, text=text)

    return {"status": "ok", "source": "fallback"}


@app.get("/health")
async def health() -> dict:
    """헬스체크 엔드포인트."""
    active_users = 0
    try:
        from src.db.session import async_session_factory
        from src.db.models import User

        async with async_session_factory() as session:
            result = await session.execute(
                select(User).where(
                    User.is_active.is_(True),
                    User.onboarding_step >= 4,
                )
            )
            active_users = len(result.scalars().all())
    except Exception:
        pass

    routes = [r.path for r in app.routes if hasattr(r, "path")]
    return {
        "status": "ok",
        "service": "tradefork",
        "version": "1.1.0",
        "users": active_users,
        "scheduler": "running" if _scheduler and _scheduler.running else "stopped",
        "bot": "running" if _tg_app else "stopped",
        "routes": routes,
    }
