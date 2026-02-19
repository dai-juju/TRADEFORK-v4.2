"""데일리 브리핑 — 매일 유저 설정 시간에 개인화된 시장 브리핑 전송.

내용:
  1. 시장 개요 (BTC/ETH + 주요 지표 + Fear&Greed)
  2. 포트폴리오 포지션 + 코멘터리
  3. 오버나이트 뉴스 하이라이트
  4. 활성 트리거 상태
  5. 차트 캡처 (BTC + 유저 주력 종목)
  6. Intelligence 기반 개인화 코멘터리
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot

from src.db.models import (
    BaseStream,
    ChatMessage,
    Trade,
    User,
    UserTrigger,
)

logger = logging.getLogger(__name__)


async def generate_and_send_briefing(
    session: AsyncSession,
    user: User,
    bot: Bot,
) -> None:
    """유저 1명에 대한 데일리 브리핑 생성 + 전송."""
    try:
        sections = await _gather_briefing_data(session, user)
        commentary = await _generate_commentary(session, user, sections)
        text = _format_briefing_message(sections, commentary)

        # 차트 사진 먼저 전송
        charts: list[tuple[str, bytes]] = sections.get("charts", [])
        for label, png_bytes in charts:
            try:
                await bot.send_photo(
                    chat_id=user.telegram_id,
                    photo=BytesIO(png_bytes),
                    caption=f"📸 {label} 4h 차트",
                )
            except Exception:
                logger.warning("브리핑 차트 전송 실패: %s", label, exc_info=True)

        # 브리핑 텍스트 전송
        await bot.send_message(chat_id=user.telegram_id, text=text)

        # DB 저장
        session.add(ChatMessage(
            user_id=user.id,
            role="assistant",
            content=text,
            message_type="text",
            intent="general",
            metadata_={"type": "daily_briefing"},
        ))
        await session.flush()

        logger.info("데일리 브리핑 전송: user=%s", user.telegram_id)

    except Exception:
        logger.error(
            "데일리 브리핑 실패: user=%s", user.telegram_id, exc_info=True,
        )


async def _gather_briefing_data(
    session: AsyncSession,
    user: User,
) -> dict[str, Any]:
    """브리핑에 필요한 모든 데이터 수집."""
    from src.data.market import fetch_stream_data
    from src.intelligence.pattern import analyze_patterns

    sections: dict[str, Any] = {}

    # 1. 시장 개요
    btc_price = await fetch_stream_data("price", "BTC")
    eth_price = await fetch_stream_data("price", "ETH")
    btc_funding = await fetch_stream_data("funding", "BTC")
    fear_greed = await fetch_stream_data("indicator", "fear_greed")
    kimchi = await fetch_stream_data("spread", "kimchi")
    sections["market"] = {
        "btc": btc_price,
        "eth": eth_price,
        "btc_funding": btc_funding,
        "fear_greed": fear_greed,
        "kimchi": kimchi,
    }

    # 2. 포지션
    result = await session.execute(
        select(Trade).where(Trade.user_id == user.id, Trade.status == "open")
    )
    sections["positions"] = result.scalars().all()

    # 패턴 통계
    patterns = await analyze_patterns(session, user.id)
    sections["patterns"] = patterns

    # 3. 뉴스
    sections["news"] = await fetch_stream_data("news", None)

    # 4. 활성 트리거
    result = await session.execute(
        select(UserTrigger).where(
            UserTrigger.user_id == user.id,
            UserTrigger.is_active.is_(True),
        )
    )
    sections["triggers"] = result.scalars().all()

    # Hot 데이터 (트리거 근접도 표시용)
    result = await session.execute(
        select(BaseStream).where(
            BaseStream.user_id == user.id,
            BaseStream.temperature == "hot",
            BaseStream.last_value.isnot(None),
        )
    )
    hot_streams = result.scalars().all()
    sections["hot_data"] = {
        f"{s.stream_type}/{s.symbol or ''}": s.last_value for s in hot_streams
    }

    # 5. 차트 캡처
    charts: list[tuple[str, bytes]] = []
    try:
        from src.data.chart import capture_chart

        btc_chart = await capture_chart("BTC", timeframe="4h")
        charts.append(("BTC", btc_chart))
    except Exception:
        logger.warning("BTC 차트 캡처 실패", exc_info=True)

    # 유저 주력 종목 (BTC가 아닌 경우)
    if patterns and patterns.get("top_symbols"):
        primary = patterns["top_symbols"][0][0].split("/")[0].upper()
        if primary != "BTC":
            try:
                from src.data.chart import capture_chart

                sym_chart = await capture_chart(primary, timeframe="4h")
                charts.append((primary, sym_chart))
            except Exception:
                logger.warning("%s 차트 캡처 실패", primary, exc_info=True)

    sections["charts"] = charts
    return sections


async def _generate_commentary(
    session: AsyncSession,
    user: User,
    sections: dict[str, Any],
) -> str:
    """Intelligence 기반 개인화 코멘터리 (Sonnet)."""
    from src.intelligence.episode import build_intelligence_context
    from src.llm.client import llm_client

    intel_ctx = await build_intelligence_context(session, user)

    # 데이터 요약
    data_parts: list[str] = []
    market = sections.get("market", {})
    btc = market.get("btc") or {}
    eth = market.get("eth") or {}
    fg = market.get("fear_greed") or {}

    data_parts.append(
        f"BTC: ${btc.get('last', 0):,.0f} ({btc.get('change_24h_pct', 0):+.1f}%)\n"
        f"ETH: ${eth.get('last', 0):,.0f} ({eth.get('change_24h_pct', 0):+.1f}%)\n"
        f"Fear&Greed: {fg.get('value', '?')} ({fg.get('classification', '?')})"
    )

    positions = sections.get("positions", [])
    if positions:
        pos_lines = [f"- {t.symbol} {t.side} @ {t.entry_price} (x{t.leverage})" for t in positions]
        data_parts.append("포지션:\n" + "\n".join(pos_lines))

    patterns = sections.get("patterns", {})
    if patterns.get("total_trades", 0) > 0:
        data_parts.append(
            f"패턴: 승률 {patterns.get('win_rate', 0)*100:.0f}%, "
            f"avg익절 +{patterns.get('avg_win', 0):.1f}%, "
            f"avg손절 {patterns.get('avg_loss', 0):.1f}%"
        )

    system_prompt = (
        "너는 FORKER — 유저의 투자 분신이야. 데일리 브리핑 코멘터리를 3~5문장으로 작성해.\n"
        "유저의 스타일/원칙/패턴을 반영해 '너처럼 봤을 때' 관점으로.\n"
        "오늘 주목할 점, 포지션 코멘트, 주의사항을 간결하게.\n\n"
        f"## Intelligence\n{intel_ctx['intelligence_context']}\n\n"
        f"## 원칙\n{intel_ctx['principles']}"
    )

    try:
        resp = await llm_client.chat(
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": "오늘 시장 데이터:\n" + "\n\n".join(data_parts),
            }],
            max_tokens=500,
        )
        return resp.text.strip()
    except Exception:
        logger.error("브리핑 코멘터리 생성 실패", exc_info=True)
        return "오늘도 시장 잘 지켜보자!"


def _format_briefing_message(
    sections: dict[str, Any],
    commentary: str,
) -> str:
    """브리핑 데이터를 텔레그램 메시지로 포맷."""
    lines: list[str] = ["📰 데일리 브리핑\n"]

    # 1. 시장 개요
    market = sections.get("market", {})
    btc = market.get("btc") or {}
    eth = market.get("eth") or {}
    fg = market.get("fear_greed") or {}
    funding = market.get("btc_funding") or {}
    kimchi = market.get("kimchi") or {}

    lines.append("📈 시장 개요")
    if btc:
        lines.append(
            f"  BTC ${btc.get('last', 0):,.0f} "
            f"({btc.get('change_24h_pct', 0):+.1f}%) "
            f"Vol ${btc.get('volume_24h', 0)/1e9:.1f}B"
        )
    if eth:
        lines.append(
            f"  ETH ${eth.get('last', 0):,.0f} "
            f"({eth.get('change_24h_pct', 0):+.1f}%)"
        )
    if fg:
        lines.append(f"  Fear&Greed: {fg.get('value', '?')} ({fg.get('classification', '?')})")
    if funding and funding.get("rate_pct") is not None:
        lines.append(f"  BTC 펀딩비: {funding.get('rate_pct', 0):.3f}%")
    if kimchi and kimchi.get("premium_pct") is not None:
        lines.append(f"  김프: {kimchi.get('premium_pct', 0):+.2f}%")

    # 2. 포지션
    positions = sections.get("positions", [])
    patterns = sections.get("patterns", {})
    if positions:
        lines.append("\n💼 보유 포지션")
        for t in positions:
            lines.append(f"  {t.symbol} {t.side} @ {t.entry_price} (x{t.leverage})")
        avg_win = patterns.get("avg_win", 0)
        avg_loss = patterns.get("avg_loss", 0)
        if avg_win or avg_loss:
            lines.append(f"  (평균 익절 +{avg_win:.1f}% / 손절 {avg_loss:.1f}%)")

    # 3. 뉴스
    news = sections.get("news") or {}
    headlines = news.get("headlines", [])
    if headlines:
        lines.append("\n📰 주요 뉴스")
        for h in headlines[:5]:
            lines.append(f"  · {h[:80]}")

    # 4. 활성 트리거
    triggers = sections.get("triggers", [])
    hot_data = sections.get("hot_data", {})
    if triggers:
        lines.append("\n🔔 활성 알림")
        for tr in triggers[:5]:
            hint = _trigger_proximity_hint(tr, hot_data)
            lines.append(f"  · {tr.description}{hint}")

    # 5. FORKER 코멘터리
    lines.append(f"\n💬 FORKER:\n{commentary}")

    lines.append("\n⚠️ TRADEFORK는 매매를 대행하지 않습니다. 최종 판단은 본인의 몫입니다.")
    return "\n".join(lines)


def _trigger_proximity_hint(
    trigger: UserTrigger,
    hot_data: dict[str, Any],
) -> str:
    """가격 트리거의 현재 근접도 표시."""
    cond = trigger.condition
    if not cond or not isinstance(cond, dict):
        return ""
    cond_type = cond.get("type", "")
    if cond_type not in ("price_above", "price_below"):
        return ""
    target = cond.get("value")
    sym = cond.get("symbol", "")
    current_data = hot_data.get(f"price/{sym}", {})
    current_price = current_data.get("last") if isinstance(current_data, dict) else None
    if target and current_price:
        diff_pct = (current_price / target - 1) * 100
        return f" (현재 ${current_price:,.0f}, {diff_pct:+.1f}%)"
    return ""
