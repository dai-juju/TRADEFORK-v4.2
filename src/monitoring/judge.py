"""Tier 3 AI 판단 — Opus로 "너처럼 봤을 때" 시그널 생성.

입력: Tier 2 수집 + Intelligence + 유저 현재 상태
출력: 매매 시그널 (direction, reasoning, counter_argument, confidence, stop_loss)
      또는 자율 브리핑
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from src.bot.keyboards import signal_feedback
from src.config import PRO_DAILY_SIGNAL_LIMIT
from src.db.models import ChatMessage, Signal, User, UserTrigger
from src.intelligence.episode import build_intelligence_context, create_episode
from src.llm.client import llm_client
from src.llm.prompts import SIGNAL_JUDGE_PROMPT

logger = logging.getLogger(__name__)


async def judge_signal(
    session: AsyncSession,
    user: User,
    collected_data: dict[str, Any],
    trigger: UserTrigger,
    bot: Bot,
) -> Signal | None:
    """Tier 3 AI 판단 → 시그널 생성 + 전송.

    Args:
        session: DB 세션
        user: 유저 객체
        collected_data: Tier 2 수집 결과
        trigger: 발동된 트리거
        bot: Telegram Bot

    Returns:
        생성된 Signal 객체 또는 None (상한 초과 등)
    """
    # 시그널 상한 체크 (Pro: 일 5회)
    if not _check_signal_limit(user):
        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=f"오늘 시그널 {PRO_DAILY_SIGNAL_LIMIT}회 다 썼어. 내일 리셋!",
            )
        except Exception:
            pass
        logger.info("시그널 상한 초과: user=%s", user.telegram_id)
        return None

    # Intelligence 컨텍스트 구축
    intel_ctx = await build_intelligence_context(session, user)
    symbol = collected_data.get("symbol") or "UNKNOWN"

    # Tier 2 데이터를 텍스트로 정리
    data_text = _format_collected_data(collected_data)

    # 시스템 프롬프트 조립
    prompt = SIGNAL_JUDGE_PROMPT.format(
        symbol=symbol,
        trigger_description=trigger.description or "",
        collected_data=data_text,
        intelligence_context=intel_ctx["intelligence_context"],
        principles=intel_ctx["principles"],
        positions=intel_ctx["positions"],
    )

    # Opus 호출
    try:
        resp = await llm_client.signal_judge(
            system=prompt,
            messages=[{
                "role": "user",
                "content": f"트리거 발동: {trigger.description}\n\n수집 데이터 기반으로 판단해줘.",
            }],
            max_tokens=2048,
        )
        raw = resp.text.strip()
        logger.info(
            "시그널 판단 LLM: in=%d (cache_read=%d), out=%d",
            resp.input_tokens, resp.cache_read_tokens, resp.output_tokens,
        )
    except Exception:
        logger.error("시그널 판단 LLM 호출 실패", exc_info=True)
        return None

    # 응답 파싱
    parsed = _parse_judge_response(raw)

    # Signal DB 저장
    signal = Signal(
        user_id=user.id,
        signal_type=parsed["signal_type"],
        content=parsed["content"],
        reasoning=parsed["reasoning"],
        counter_argument=parsed.get("counter_argument"),
        confidence=parsed["confidence"],
        confidence_style=parsed.get("confidence_style"),
        confidence_history=parsed.get("confidence_history"),
        confidence_market=parsed.get("confidence_market"),
        symbol=symbol,
        direction=parsed.get("direction"),
        stop_loss=parsed.get("stop_loss"),
    )
    session.add(signal)

    # 일일 시그널 카운트 증가
    user.daily_signal_count = (user.daily_signal_count or 0) + 1
    await session.flush()

    # 시그널 메시지 포맷
    text = _format_signal_message(parsed, symbol)

    # DB에 assistant 메시지 저장
    session.add(ChatMessage(
        user_id=user.id,
        role="assistant",
        content=text,
        message_type="text",
        intent="signal_trigger",
        metadata_={"signal_id": signal.id},
    ))
    await session.flush()

    # 차트 이미지 전송 (있으면)
    chart_png = collected_data.get("chart_png")

    try:
        if chart_png:
            from io import BytesIO

            await bot.send_photo(
                chat_id=user.telegram_id,
                photo=BytesIO(chart_png),
                caption=f"📸 {symbol} 차트",
            )

        # 시그널 텍스트 + 피드백 키보드 전송
        await bot.send_message(
            chat_id=user.telegram_id,
            text=text,
            reply_markup=signal_feedback(),
        )
    except Exception:
        logger.error("시그널 전송 실패: user=%s", user.telegram_id, exc_info=True)

    # 에피소드 생성
    await create_episode(
        session,
        user,
        episode_type="signal",
        user_action=f"시그널: {symbol} {parsed.get('direction', 'watch')}",
        embedding_text=f"{symbol} {parsed['reasoning'][:300]}",
        reasoning=parsed["reasoning"],
        auto_collect_market=True,
    )

    logger.info(
        "시그널 생성: user=%s, %s %s (conf=%.0f%%)",
        user.telegram_id, symbol,
        parsed.get("direction", "watch"),
        parsed["confidence"] * 100,
    )
    return signal


# ------------------------------------------------------------------
# 시그널 상한 체크
# ------------------------------------------------------------------


def _check_signal_limit(user: User) -> bool:
    """일일 시그널 상한 체크 + 리셋."""
    now = datetime.now(timezone.utc)

    # 날짜가 바뀌었으면 리셋
    if user.daily_signal_reset_at:
        if user.daily_signal_reset_at.date() < now.date():
            user.daily_signal_count = 0
            user.daily_signal_reset_at = now
    else:
        user.daily_signal_reset_at = now

    return (user.daily_signal_count or 0) < PRO_DAILY_SIGNAL_LIMIT


# ------------------------------------------------------------------
# 응답 파싱
# ------------------------------------------------------------------


def _parse_confidence(raw_conf: Any) -> dict[str, float | None]:
    """confidence를 파싱 — 3축 dict 또는 단일 float 모두 처리.

    Returns:
        confidence: overall 가중평균
        confidence_style/history/market: 각 축 (없으면 None)
    """
    if isinstance(raw_conf, dict):
        style = float(raw_conf.get("style_match", 0.5))
        history = float(raw_conf.get("historical_similar", 0.5))
        market = float(raw_conf.get("market_context", 0.5))
        overall = style * 0.3 + history * 0.3 + market * 0.4
        return {
            "confidence": overall,
            "confidence_style": style,
            "confidence_history": history,
            "confidence_market": market,
        }
    val = float(raw_conf) if raw_conf else 0.5
    if val > 1:
        val = val / 100
    return {
        "confidence": val,
        "confidence_style": None,
        "confidence_history": None,
        "confidence_market": None,
    }


def _parse_judge_response(raw: str) -> dict[str, Any]:
    """Opus 응답을 구조화된 시그널로 파싱.

    Opus는 JSON 블록 또는 자연어로 응답할 수 있으므로 양쪽 모두 처리.
    """
    import re

    # JSON 블록 추출 시도
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            conf = _parse_confidence(data.get("confidence", 0.5))
            return {
                "signal_type": data.get("signal_type", "trade_signal"),
                "direction": data.get("direction", "watch"),
                "reasoning": data.get("reasoning", raw[:500]),
                "counter_argument": data.get("counter_argument"),
                "stop_loss": data.get("stop_loss"),
                "content": _build_content(data, raw),
                **conf,
            }
        except (json.JSONDecodeError, ValueError):
            pass

    # 자연어 파싱 fallback
    direction = "watch"
    for d, keywords in [
        ("long", ["롱", "long", "매수", "진입"]),
        ("short", ["숏", "short", "매도"]),
        ("exit", ["청산", "exit", "탈출"]),
    ]:
        if any(kw in raw.lower() for kw in keywords):
            direction = d
            break

    # confidence 추출
    conf_match = re.search(r"확신도[:\s]*(\d+)", raw)
    confidence = float(conf_match.group(1)) / 100 if conf_match else 0.5
    if confidence > 1:
        confidence = confidence / 100

    # counter_argument 추출
    counter = None
    counter_match = re.search(r"(?:반대|⚠️).*?[:：]\s*(.+?)(?:\n\n|\Z)", raw, re.DOTALL)
    if counter_match:
        counter = counter_match.group(1).strip()[:500]

    # stop_loss 추출
    sl_match = re.search(r"손절[:\s]*(.+?)(?:\n|$)", raw)
    stop_loss = sl_match.group(1).strip() if sl_match else None

    # 브리핑인지 시그널인지
    is_briefing = any(kw in raw.lower() for kw in ["브리핑", "briefing", "참고"])
    signal_type = "briefing" if is_briefing else "trade_signal"

    return {
        "signal_type": signal_type,
        "direction": direction,
        "reasoning": raw[:1000],
        "counter_argument": counter,
        "confidence": confidence,
        "confidence_style": None,
        "confidence_history": None,
        "confidence_market": None,
        "stop_loss": stop_loss,
        "content": raw[:2000],
    }


def _build_content(data: dict[str, Any], raw: str) -> str:
    """JSON 데이터에서 content 필드 생성."""
    parts = []
    if data.get("reasoning"):
        parts.append(data["reasoning"])
    if data.get("counter_argument"):
        parts.append(f"반대 근거: {data['counter_argument']}")
    return "\n\n".join(parts) if parts else raw[:1000]


# ------------------------------------------------------------------
# 시그널 메시지 포맷
# ------------------------------------------------------------------


def _confidence_bar(label: str, value: float) -> str:
    """Unicode 막대 그래프 한 줄 생성."""
    filled = round(value * 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"  {label}  {bar}  {value*100:.0f}%"


def _format_signal_message(parsed: dict[str, Any], symbol: str) -> str:
    """시그널을 텔레그램 메시지로 포맷 — 3축 확신도 바 그래프."""
    direction = parsed.get("direction", "watch")
    dir_emoji = {
        "long": "🟢 롱",
        "short": "🔴 숏",
        "exit": "🚪 청산",
        "watch": "👀 관망",
    }.get(direction, "👀 관망")

    overall = parsed.get("confidence", 0.5)

    lines = [f"🎯 {symbol} {dir_emoji} 상황"]
    lines.append("")
    lines.append(f"📊 판단 근거:\n{parsed.get('reasoning', '')[:800]}")

    counter = parsed.get("counter_argument") or "반대 시나리오도 항상 존재해. 리스크 관리 필수."
    lines.append(f"\n⚠️ 반대 근거:\n{counter[:400]}")

    lines.append(f"\n📍 확신도: {overall*100:.0f}%")

    style = parsed.get("confidence_style")
    history = parsed.get("confidence_history")
    market = parsed.get("confidence_market")
    if style is not None and history is not None and market is not None:
        lines.append(_confidence_bar("스타일 매칭", style))
        lines.append(_confidence_bar("유사 과거 ", history))
        lines.append(_confidence_bar("시장 맥락 ", market))

    stop = parsed.get("stop_loss")
    if stop:
        lines.append(f"\n🛑 손절: {stop}")

    lines.append("\n어떻게 생각해?")
    lines.append("\n⚠️ TRADEFORK는 매매를 대행하지 않습니다. 최종 판단은 본인의 몫입니다.")

    return "\n".join(lines)


# ------------------------------------------------------------------
# 수집 데이터 텍스트 포맷
# ------------------------------------------------------------------


def _format_collected_data(collected: dict[str, Any]) -> str:
    """Tier 2 수집 데이터를 LLM 프롬프트용 텍스트로 변환."""
    parts: list[str] = []

    # Base 데이터
    base = collected.get("base_data", {})
    if base:
        base_lines = []
        for k, v in base.items():
            if isinstance(v, (dict, list)):
                base_lines.append(f"- {k}: {json.dumps(v, ensure_ascii=False, default=str)[:300]}")
            else:
                base_lines.append(f"- {k}: {v}")
        parts.append("## Base 데이터\n" + "\n".join(base_lines[:15]))

    # API 데이터
    api = collected.get("api_data", {})
    if api:
        api_lines = []
        for k, v in api.items():
            if isinstance(v, (dict, list)):
                api_lines.append(f"- {k}: {json.dumps(v, ensure_ascii=False, default=str)[:300]}")
            else:
                api_lines.append(f"- {k}: {v}")
        parts.append("## 외부 API\n" + "\n".join(api_lines[:10]))

    # 검색 데이터
    search = collected.get("search_data")
    if search:
        parts.append(f"## 웹 검색\n{search[:1500]}")

    return "\n\n".join(parts) if parts else "수집 데이터 없음"
