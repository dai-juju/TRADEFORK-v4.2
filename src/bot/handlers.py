"""텔레그램 봇 핸들러 — /start, /sync, /principles, /help + 메시지 + 콜백."""

from __future__ import annotations

import logging
import time

from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src.bot.keyboards import add_more_exchange, briefing_time_selection, exchange_selection
from src.core.auth import get_or_create_user
from src.core.chat import process_message
from src.core.onboarding import (
    analyze_trades_30d,
    get_exchange_guide,
    handle_api_key_input,
    handle_principles_edit,
    handle_style_input,
)
from src.core.sync_rate import calculate_sync_rate, format_sync_rate
from src.db.models import ChatMessage, Principle
from src.db.session import async_session_factory

logger = logging.getLogger(__name__)

PRINCIPLES_TIMEOUT = 60  # seconds


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — 온보딩 시작."""
    if not update.effective_user or not update.message:
        return

    tg_id = update.effective_user.id
    username = update.effective_user.username

    async with async_session_factory() as session:
        user, is_new = await get_or_create_user(session, tg_id, username)

        if not is_new and user.onboarding_step >= 4:
            await update.message.reply_text("이미 등록됐어! 궁금한 거 물어봐 😎")
            await session.commit()
            return

        # 이미 거래소가 연결되어 있으면 → 바로 분석 단계로
        from sqlalchemy import func, select as sa_select
        from src.core.onboarding import analyze_trades_30d
        from src.db.models import ExchangeConnection

        conn_count_row = await session.execute(
            sa_select(func.count()).where(
                ExchangeConnection.user_id == user.id,
                ExchangeConnection.is_active.is_(True),
            )
        )
        has_connections = (conn_count_row.scalar() or 0) > 0

        if has_connections and user.onboarding_step < 4:
            user.onboarding_step = 2
            await session.flush()
            await update.message.reply_text(
                "좋아! 최근 한 달 매매 내역을 분석해볼게... ⏳"
            )
            report = await analyze_trades_30d(session, user)
            user.onboarding_step = 3
            await session.flush()
            session.add(
                ChatMessage(
                    user_id=user.id,
                    role="assistant",
                    content=report,
                    message_type="text",
                )
            )
            await update.message.reply_text(report)
            await session.commit()
            return

        # 새 유저 또는 거래소 미연결 → 온보딩 시작
        user.onboarding_step = 1
        await session.flush()

        msg = (
            "안녕! FORKER야. 너의 투자 분신이 될게 🔥\n"
            "먼저 거래소를 연결하자.\n\n"
            "📌 사용하는 거래소의 API를 등록해줘. <b>읽기전용</b>만 필요해!\n"
            "⚠️ TRADEFORK는 절대 매매를 대행하지 않아. 출금/주문 권한 불필요.\n\n"
            "등록할 거래소를 선택해:"
        )
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=exchange_selection(),
        )
        await session.commit()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — 도움말."""
    if not update.message:
        return

    msg = (
        "🔧 TRADEFORK 명령어\n\n"
        "/start — 처음 시작 + 온보딩\n"
        "/sync — 싱크로율 조회 (FORKER가 너를 얼마나 아는지)\n"
        "/principles — 투자 원칙 조회/수정 (추가/수정/삭제 자유)\n"
        "/dailybrief — 데일리 브리핑 시간 설정/변경\n"
        "/help — 이 안내\n\n"
        "💡 명령어 없이 자유롭게 대화해도 돼!\n\n"
        "📊 시장 질문\n"
        "  · 'VANA 왜 올라?'\n"
        "  · 'ETH 펀딩비 어때?'\n\n"
        "🔔 시장 요청 (알림 + 브리핑)\n"
        "  · 'BTC 10만 되면 알려줘'\n"
        "  · '업비트 거래량 상위 코인 3개가 비트코인보다 높으면 알려줘'\n"
        "  · '거래대금 터지면 분석해줘'\n"
        "  · 'SOL 펀딩비 -0.1% 이하면 브리핑'\n\n"
        "📸 차트 분석\n"
        "  · 차트 캡처 보내면 패턴/지지·저항 분석\n\n"
        "🔄 복기\n"
        "  · '어제 SOL 매매 복기해줘'\n\n"
        "💬 잡담도 OK — 전부 FORKER 학습에 반영돼!\n\n"
        "⚠️ TRADEFORK는 매매를 대행하지 않습니다. 최종 판단은 본인의 몫입니다."
    )
    await update.message.reply_text(msg)


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sync — 싱크로율 조회."""
    if not update.effective_user or not update.message:
        return

    tg_id = update.effective_user.id

    async with async_session_factory() as session:
        user, _ = await get_or_create_user(session, tg_id, update.effective_user.username)

        if user.onboarding_step < 4:
            await update.message.reply_text(
                "아직 온보딩이 완료되지 않았어. /start 로 시작해봐!"
            )
            await session.commit()
            return

        sync_data = await calculate_sync_rate(session, user)
        sync_text = format_sync_rate(sync_data)
        await update.message.reply_text(sync_text)
        await session.commit()


async def principles_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/principles — 투자 원칙 조회/수정."""
    if not update.effective_user or not update.message:
        return

    tg_id = update.effective_user.id

    async with async_session_factory() as session:
        user, _ = await get_or_create_user(
            session, tg_id, update.effective_user.username
        )

        if user.onboarding_step < 4:
            await update.message.reply_text(
                "아직 온보딩이 완료되지 않았어. /start 로 시작해봐!"
            )
            await session.commit()
            return

        # 원칙 조회
        result = await session.execute(
            select(Principle).where(
                Principle.user_id == user.id, Principle.is_active.is_(True)
            )
        )
        current = result.scalars().all()

        if current:
            lines = [f"{i + 1}. {p.content}" for i, p in enumerate(current)]
            msg = (
                "📋 너의 투자 원칙:\n"
                + "\n".join(lines)
                + "\n\n추가, 수정, 삭제 자유롭게 말해!"
            )
        else:
            msg = (
                "아직 원칙이 없어. 자유롭게 입력해봐!\n"
                "예시: '손절 -5%, 레버리지 최대 10배, 펀딩비 음수일 때만 롱'"
            )

        await update.message.reply_text(msg)
        await session.commit()

    # principles_editing 플래그 + 타임스탬프 설정
    context.user_data["principles_editing"] = True
    context.user_data["principles_editing_at"] = time.time()


async def dailybrief_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/dailybrief — 데일리 브리핑 시간 설정."""
    if not update.effective_user or not update.message:
        return

    tg_id = update.effective_user.id

    async with async_session_factory() as session:
        user, _ = await get_or_create_user(
            session, tg_id, update.effective_user.username
        )

        if user.onboarding_step < 4:
            await update.message.reply_text(
                "아직 온보딩이 완료되지 않았어. /start 로 시작해봐!"
            )
            await session.commit()
            return

        current = user.briefing_hour
        if current is not None:
            status = f"현재 설정: 매일 {current}:00 KST"
        else:
            status = "현재 설정: OFF (브리핑 비활성)"

        msg = (
            f"📰 데일리 브리핑\n\n"
            f"{status}\n\n"
            f"시간을 선택하거나 숫자(0~23)를 직접 입력해:"
        )
        await update.message.reply_text(
            msg,
            reply_markup=briefing_time_selection(current),
        )
        await session.commit()

    context.user_data["briefing_editing"] = True
    context.user_data["briefing_editing_at"] = time.time()


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """일반 메시지 처리 — 온보딩 단계별 분기 + principles 편집 + Q2 채팅."""
    if not update.effective_user or not update.message or not update.message.text:
        return

    tg_id = update.effective_user.id
    text = update.message.text.strip()

    if not text:
        return

    async with async_session_factory() as session:
        user, _ = await get_or_create_user(
            session, tg_id, update.effective_user.username
        )

        # 유저 메시지 DB 저장
        session.add(
            ChatMessage(
                user_id=user.id,
                role="user",
                content=text,
                message_type="text",
                telegram_message_id=update.message.message_id,
            )
        )
        await session.flush()

        # --- 온보딩 중 ---
        # Step 1: 거래소 API 키 입력 대기
        if user.onboarding_step == 1:
            exchange_name = context.user_data.get("pending_exchange")
            if not exchange_name:
                await update.message.reply_text(
                    "거래소를 먼저 선택해줘!",
                    reply_markup=exchange_selection(),
                )
                await session.commit()
                return

            success, msg = await handle_api_key_input(
                session, user, exchange_name, text
            )
            if success:
                context.user_data.pop("pending_exchange", None)
                await update.message.reply_text(
                    msg, reply_markup=add_more_exchange()
                )
            else:
                await update.message.reply_text(msg)
            await session.commit()
            return

        # Step 3: 스타일 + 원칙 자유 입력
        if user.onboarding_step == 3:
            reply = await handle_style_input(session, user, text)
            session.add(
                ChatMessage(
                    user_id=user.id,
                    role="assistant",
                    content=reply,
                    message_type="text",
                )
            )
            await update.message.reply_text(reply)
            await session.commit()
            return

        # --- 온보딩 완료된 유저 ---
        if user.onboarding_step >= 4:
            # /principles 편집 모드 (60초 타임아웃)
            editing = context.user_data.get("principles_editing", False)
            editing_at = context.user_data.get("principles_editing_at", 0)
            if editing and (time.time() - editing_at < PRINCIPLES_TIMEOUT):
                reply = await handle_principles_edit(session, user, text)
                session.add(
                    ChatMessage(
                        user_id=user.id,
                        role="assistant",
                        content=reply,
                        message_type="text",
                    )
                )
                await update.message.reply_text(reply)
                await session.commit()
                return

            # 타임아웃 or 편집 모드 아님 → 플래그 정리
            context.user_data.pop("principles_editing", None)
            context.user_data.pop("principles_editing_at", None)

            # /dailybrief 직접 입력 모드 (60초 타임아웃)
            brief_editing = context.user_data.get("briefing_editing", False)
            brief_editing_at = context.user_data.get("briefing_editing_at", 0)
            if brief_editing and (time.time() - brief_editing_at < PRINCIPLES_TIMEOUT):
                context.user_data.pop("briefing_editing", None)
                context.user_data.pop("briefing_editing_at", None)
                reply: str | None = None
                if text.lower() == "off":
                    user.briefing_hour = None
                    reply = "📰 데일리 브리핑 OFF."
                else:
                    try:
                        hour = int(text)
                        if 0 <= hour <= 23:
                            user.briefing_hour = hour
                            reply = f"📰 매일 {hour}:00 KST에 브리핑 보내줄게!"
                        else:
                            reply = "0~23 사이 숫자를 입력해줘."
                    except ValueError:
                        pass  # 숫자가 아니면 일반 채팅으로 진행
                if reply is not None:
                    session.add(ChatMessage(
                        user_id=user.id, role="assistant",
                        content=reply, message_type="text",
                    ))
                    await update.message.reply_text(reply)
                    await session.commit()
                    return
            else:
                context.user_data.pop("briefing_editing", None)
                context.user_data.pop("briefing_editing_at", None)

            # 시그널 피드백 자연어 대기 중
            if context.user_data.pop("awaiting_signal_feedback", False):
                from src.feedback.processor import process_signal_feedback

                sig_id = context.user_data.pop("last_signal_id", None)
                if sig_id:
                    await process_signal_feedback(
                        session, user, sig_id, user_feedback=text,
                    )
                reply = "피드백 반영했어! FORKER가 더 잘 배울게."
                session.add(ChatMessage(
                    user_id=user.id, role="assistant",
                    content=reply, message_type="text",
                ))
                await update.message.reply_text(reply)
                await session.commit()
                return

            # 매매 근거 자연어 대기 중
            if context.user_data.pop("awaiting_trade_reason", False):
                from src.exchange.trade_detector import save_user_reasoning

                await save_user_reasoning(session, user, text)
                reply = "알겠어! 네 진짜 이유를 기록했어."
                session.add(ChatMessage(
                    user_id=user.id, role="assistant",
                    content=reply, message_type="text",
                ))
                await update.message.reply_text(reply)
                await session.commit()
                return

            # Q2 채팅 엔진 — "생각 중" 메시지 먼저 전송
            await update.message.chat.send_action(ChatAction.TYPING)
            thinking_msg = await update.message.reply_text("💭 생각하는 중...")
            try:
                result = await process_message(session, user, text)
                reply_text = result.response_text
            except Exception:
                logger.error("채팅 처리 실패", exc_info=True)
                reply_text = "잠깐 문제가 생겼어. 다시 말해줘!"
            try:
                await thinking_msg.edit_text(reply_text)
            except Exception:
                try:
                    await thinking_msg.delete()
                except Exception:
                    pass
                await update.message.reply_text(reply_text)
            try:
                await session.commit()
            except Exception:
                pass
            return

        # 기타 (온보딩 중간 단계 — step 0, 2 등)
        await update.message.reply_text(
            "현재 진행 중인 온보딩 단계가 있어. /start 로 다시 시작해봐!"
        )
        await session.commit()


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """사진 메시지 처리 — 차트 이미지 분석."""
    if not update.effective_user or not update.message or not update.message.photo:
        return

    tg_id = update.effective_user.id
    caption = (update.message.caption or "").strip()

    # 가장 큰 사이즈 사진 다운로드
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()

    async with async_session_factory() as session:
        user, _ = await get_or_create_user(
            session, tg_id, update.effective_user.username
        )

        if user.onboarding_step < 4:
            await update.message.reply_text(
                "온보딩을 먼저 완료해줘! /start"
            )
            await session.commit()
            return

        # 유저 메시지 저장
        session.add(
            ChatMessage(
                user_id=user.id,
                role="user",
                content=caption or "[차트 이미지]",
                message_type="image",
                telegram_message_id=update.message.message_id,
            )
        )
        await session.flush()

        # Q2 채팅 엔진 (Vision 모드) — "분석 중" 메시지 먼저 전송
        await update.message.chat.send_action(ChatAction.TYPING)
        thinking_msg = await update.message.reply_text("🔍 차트 분석 중...")
        result = await process_message(
            session,
            user,
            message_text=caption or "이 차트를 분석해줘.",
            image_data=bytes(image_bytes),
            image_media_type="image/jpeg",
        )
        try:
            await thinking_msg.edit_text(result.response_text)
        except Exception:
            try:
                await thinking_msg.delete()
            except Exception:
                pass
            await update.message.reply_text(result.response_text)
        await session.commit()


async def callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """인라인 키보드 콜백 처리 — 거래소 선택, 매매근거 확인, 시그널 피드백."""
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return

    await query.answer()
    data = query.data
    tg_id = update.effective_user.id

    # --- 거래소 선택 ---
    if data.startswith("ex:"):
        action = data[3:]

        # 온보딩 단계 확인 (step 1에서만 거래소 등록 가능)
        async with async_session_factory() as session:
            user, _ = await get_or_create_user(
                session, tg_id, update.effective_user.username
            )

            if action == "more":
                if user.onboarding_step >= 4:
                    await query.edit_message_text("이미 온보딩이 완료됐어!")
                    await session.commit()
                    return
                await query.edit_message_text(
                    "등록할 거래소를 선택해:",
                    reply_markup=exchange_selection(),
                )
                await session.commit()
                return

            if action == "done":
                if user.onboarding_step >= 3:
                    await query.edit_message_text("이미 처리됐어!")
                    await session.commit()
                    return

                user.onboarding_step = 2
                await session.flush()

                await query.edit_message_text(
                    "좋아! 최근 한 달 매매 내역을 분석해볼게... ⏳"
                )

                report = await analyze_trades_30d(session, user)
                user.onboarding_step = 3
                await session.flush()

                session.add(
                    ChatMessage(
                        user_id=user.id,
                        role="assistant",
                        content=report,
                        message_type="text",
                    )
                )
                await session.commit()

                await context.bot.send_message(chat_id=tg_id, text=report)
                return

            # 특정 거래소 선택
            if action in ("binance", "upbit", "bithumb"):
                if user.onboarding_step >= 4:
                    await query.edit_message_text("이미 온보딩이 완료됐어!")
                    await session.commit()
                    return
                context.user_data["pending_exchange"] = action
                guide = await get_exchange_guide(action)
                await query.edit_message_text(guide, parse_mode="HTML")
                await session.commit()
                return

            await session.commit()

    # --- 매매 근거 확인 ---
    if data.startswith("reason:"):
        confirmed = data == "reason:yes"
        async with async_session_factory() as session:
            user, _ = await get_or_create_user(
                session, tg_id, update.effective_user.username
            )
            from src.exchange.trade_detector import confirm_trade_reasoning

            await confirm_trade_reasoning(session, user, confirmed)
            await session.commit()

        if confirmed:
            await query.edit_message_text(
                "✅ 확인했어! FORKER가 점점 너를 이해하고 있어."
            )
        else:
            await query.edit_message_text(
                "알겠어. 실제 이유를 알려주면 더 잘 배울 수 있어!"
            )
            context.user_data["awaiting_trade_reason"] = True
        return

    # --- 시그널 피드백 ---
    if data.startswith("sig:"):
        agreed = data == "sig:agree"
        async with async_session_factory() as session:
            from src.db.models import Signal
            from src.feedback.processor import process_signal_feedback

            user, _ = await get_or_create_user(
                session, tg_id, update.effective_user.username,
            )
            result = await session.execute(
                select(Signal)
                .where(Signal.user_id == user.id)
                .order_by(Signal.created_at.desc())
                .limit(1)
            )
            signal = result.scalar_one_or_none()
            if signal:
                await process_signal_feedback(
                    session, user, signal.id, agreed=agreed,
                )
                await session.commit()
                context.user_data["last_signal_id"] = signal.id

        if agreed:
            await query.edit_message_text("✅ 동의 피드백 기록했어!")
        else:
            await query.edit_message_text(
                "❌ 반대 의견 기록했어. 이유를 말해주면 더 잘 배울 수 있어!"
            )
            context.user_data["awaiting_signal_feedback"] = True
        return

    # --- 브리핑 시간 설정 ---
    if data.startswith("brief:"):
        value = data[6:]
        async with async_session_factory() as session:
            user, _ = await get_or_create_user(
                session, tg_id, update.effective_user.username
            )
            if value == "off":
                user.briefing_hour = None
                await session.commit()
                await query.edit_message_text("📰 데일리 브리핑 OFF. 다시 켜려면 /dailybrief")
            else:
                hour = int(value)
                user.briefing_hour = hour
                await session.commit()
                await query.edit_message_text(
                    f"📰 매일 {hour}:00 KST에 브리핑 보내줄게!"
                )
        return

    # --- 미처리 콜백 ---
    logger.warning("미처리 콜백 데이터: %s", data)
