"""온보딩 전체 플로우 — /start → 거래소 등록 → 30일 분석 → 스타일/원칙 입력."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import connect_exchange
from src.core.sync_rate import calculate_sync_rate, format_sync_rate
from src.db.models import Principle, User
from src.llm.client import llm_client

logger = logging.getLogger(__name__)

# --- 서버 공개 IP 캐시 ---
_server_ip: str | None = None


async def get_server_ip() -> str:
    """서버의 공개 IP를 가져와 캐시. STATIC_IP 환경변수가 있으면 우선 사용."""
    global _server_ip
    if _server_ip:
        return _server_ip

    from src.config import STATIC_IP

    if STATIC_IP:
        _server_ip = STATIC_IP
        return _server_ip

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("https://api.ipify.org")
            resp.raise_for_status()
            _server_ip = resp.text.strip()
    except Exception:
        logger.warning("서버 IP 조회 실패")
        _server_ip = "(확인 실패 — 관리자에게 문의)"
    return _server_ip


async def get_exchange_guide(exchange_name: str) -> str | None:
    """거래소별 API 등록 안내 (서버 IP 포함)."""
    ip = await get_server_ip()

    guides: dict[str, str] = {
        "binance": (
            "바이낸스 API 등록 방법:\n"
            "1. binance.com → API Management\n"
            "2. 'Create API' → Label 입력\n"
            "3. ⚠️ 권한: 'Enable Reading'만 체크! 나머지 전부 OFF\n"
            "4. Edit restrictions → <b>IP access restrictions</b> 선택\n"
            f"5. 허용 IP에 <code>{ip}</code> 입력\n"
            "6. API Key와 Secret Key를 아래 형식으로 보내줘:\n\n"
            "<code>API_KEY:SECRET_KEY</code>\n\n"
            "(한 줄에 콜론으로 구분)"
        ),
        "upbit": (
            "업비트 API 등록 방법:\n"
            "1. upbit.com → 마이페이지 → <b>Open API 관리</b>\n"
            "2. '자산조회'만 체크! 나머지 OFF\n"
            f"3. 허용 IP 주소에 <code>{ip}</code> 입력\n"
            "4. Access Key와 Secret Key를 아래 형식으로 보내줘:\n\n"
            "<code>ACCESS_KEY:SECRET_KEY</code>\n\n"
            "(한 줄에 콜론으로 구분)"
        ),
        "bithumb": (
            "빗썸 API 등록 방법:\n"
            "1. bithumb.com → 마이페이지 → <b>API 관리</b>\n"
            "2. '조회' 권한만 활성화! 나머지 OFF\n"
            f"3. IP 주소 등록에 <code>{ip}</code> 입력\n"
            "4. API Key와 Secret Key를 아래 형식으로 보내줘:\n\n"
            "<code>API_KEY:SECRET_KEY</code>\n\n"
            "(한 줄에 콜론으로 구분)"
        ),
    }
    return guides.get(exchange_name)


async def handle_api_key_input(
    session: AsyncSession,
    user: User,
    exchange_name: str,
    text: str,
) -> tuple[bool, str]:
    """API 키 입력 처리. Returns (success, message)."""
    # 형식 검증
    if ":" not in text:
        return False, "형식이 안 맞아. <code>API_KEY:SECRET_KEY</code> 형태로 보내줘."

    parts = text.strip().split(":", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return False, "API Key와 Secret Key를 콜론(:)으로 구분해서 보내줘."

    api_key = parts[0].strip()
    api_secret = parts[1].strip()

    err = await connect_exchange(session, user, exchange_name, api_key, api_secret)
    if err:
        # "이미 연결"은 에러가 아님 — 거래소 연결 상태이므로 성공 처리
        if "이미 연결" in err:
            return True, f"✅ {exchange_name}은 이미 연결되어 있어!"
        return False, err

    return True, f"✅ {exchange_name} 연결 완료!"


async def _fetch_all_orders(
    ex: Any, exchange_name: str, since_ms: int
) -> list[dict]:
    """거래소에서 전체 심볼의 체결 내역 조회. 30일 이내 필터링."""
    from datetime import datetime, timezone

    orders: list[dict] = []

    if exchange_name == "upbit":
        # 업비트: privateGetOrdersClosed — 심볼 지정 없이 전체 조회
        try:
            raw = await ex.privateGetOrdersClosed({"limit": 100})
            for o in raw:
                state = o.get("state", "")
                if state != "done":
                    continue
                # 타임스탬프 파싱 (ISO format: 2026-02-17T00:21:28+09:00)
                created = o.get("created_at", "")
                ts = 0
                if created:
                    try:
                        dt = datetime.fromisoformat(created)
                        ts = int(dt.timestamp() * 1000)
                    except Exception:
                        pass
                if ts < since_ms:
                    continue
                # KRW-XRP → XRP/KRW 변환
                market = o.get("market", "")
                symbol = ""
                if market.startswith("KRW-"):
                    symbol = f"{market[4:]}/KRW"
                elif market.startswith("BTC-"):
                    symbol = f"{market[4:]}/BTC"
                elif market.startswith("USDT-"):
                    symbol = f"{market[5:]}/USDT"
                orders.append({
                    "symbol": symbol,
                    "side": "buy" if o.get("side") == "bid" else "sell",
                    "amount": float(o.get("executed_volume") or o.get("volume") or 0),
                    "cost": float(o.get("executed_funds") or 0),
                    "timestamp": ts,
                    "status": "closed",
                    "_exchange": exchange_name,
                    "info": o,
                })
        except Exception:
            logger.warning("업비트 전체 주문 조회 실패")

    elif exchange_name == "binance":
        # 바이낸스: 모든 USDT/BUSD 마켓의 체결 내역
        all_symbols = [s for s in ex.markets if s.endswith("/USDT")]
        for sym in all_symbols:
            try:
                trades = await ex.fetch_my_trades(sym, since=since_ms, limit=100)
                for t in trades:
                    t["_exchange"] = exchange_name
                orders.extend(trades)
            except Exception:
                pass

    elif exchange_name == "bithumb":
        # 빗썸: fetchClosedOrders 심볼별 순회 (KRW 마켓)
        all_symbols = [s for s in ex.markets if s.endswith("/KRW")]
        for sym in all_symbols:
            try:
                closed = await ex.fetch_closed_orders(sym, limit=100)
                for o in closed:
                    ts = o.get("timestamp") or 0
                    if ts >= since_ms:
                        o["_exchange"] = exchange_name
                        orders.append(o)
            except Exception:
                pass

    else:
        # 기타 거래소: fetchClosedOrders 시도
        try:
            closed = await ex.fetch_closed_orders(None, limit=100)
            for o in closed:
                ts = o.get("timestamp") or 0
                if ts >= since_ms:
                    o["_exchange"] = exchange_name
                    orders.append(o)
        except Exception:
            logger.warning("%s 주문 조회 실패", exchange_name)

    logger.info("%s에서 %d건 매매 내역 조회", exchange_name, len(orders))
    return orders


async def analyze_trades_30d(
    session: AsyncSession,
    user: User,
) -> str:
    """30일 매매 내역 분석 → 초기 리포트."""
    import time as _time
    from datetime import datetime, timedelta, timezone

    import aiohttp
    from aiohttp.resolver import ThreadedResolver
    from sqlalchemy import select

    import ccxt.async_support as ccxt_async

    from src.db.models import ExchangeConnection
    from src.security.encryption import decrypt

    since_dt = datetime.now(timezone.utc) - timedelta(days=30)
    since_ms = int(since_dt.timestamp() * 1000)

    # 연결된 거래소 조회
    result = await session.execute(
        select(ExchangeConnection).where(
            ExchangeConnection.user_id == user.id,
            ExchangeConnection.is_active.is_(True),
        )
    )
    connections = result.scalars().all()

    if not connections:
        return (
            "📊 너의 30일 매매 분석 리포트\n\n"
            "연결된 거래소가 없어서 매매 내역을 가져올 수 없었어.\n\n"
            "이제 너의 투자 스타일과 지키는 원칙을 자유롭게 알려줘!\n"
            "한 번에 다 말해도 돼.\n\n"
            "예시: '선물 위주로 하고 펀딩비 보고 들어가. 주로 SOL ETH. "
            "손절 -5%, 한 종목 30% 이상 안 넣어.'"
        )

    all_orders: list[dict] = []
    balance_summary: dict[str, float] = {}

    for conn in connections:
        api_key = decrypt(conn.api_key_encrypted)
        api_secret = decrypt(conn.api_secret_encrypted)

        exchange_cls = getattr(ccxt_async, conn.exchange, None)
        if not exchange_cls:
            continue

        connector = aiohttp.TCPConnector(resolver=ThreadedResolver())
        http_session = aiohttp.ClientSession(connector=connector)
        ex = exchange_cls({
            "apiKey": api_key,
            "secret": api_secret,
            "session": http_session,
        })

        try:
            await ex.load_markets()

            # 잔고 조회
            try:
                bal = await ex.fetch_balance()
                for symbol, info in bal.get("total", {}).items():
                    if info and float(info) > 0 and symbol not in ("KRW", "USDT", "BUSD"):
                        balance_summary[symbol] = (
                            balance_summary.get(symbol, 0) + float(info)
                        )
            except Exception:
                logger.warning("잔고 조회 실패: %s", conn.exchange)

            # 매매 내역 조회 — 거래소별 전체 심볼 커버
            try:
                fetched = await _fetch_all_orders(ex, conn.exchange, since_ms)
                all_orders.extend(fetched)
            except Exception:
                logger.warning("매매 내역 조회 실패: %s", conn.exchange)
        finally:
            del api_key, api_secret
            await ex.close()
            await http_session.close()

    # --- 분석 ---
    holding_coins = sorted(balance_summary.keys())
    total_trades = len(all_orders)

    # 기본 통계 계산
    symbols_traded: dict[str, int] = {}
    buy_count = 0
    sell_count = 0
    total_volume = 0.0

    for order in all_orders:
        sym = order.get("symbol", "")
        side = order.get("side", "")
        # cost가 None인 경우 (시장가 주문) → info.executed_funds에서 추출
        cost = order.get("cost")
        if not cost:
            info = order.get("info", {})
            cost = info.get("executed_funds") or info.get("cummulativeQuoteQty") or 0

        if sym:
            symbols_traded[sym] = symbols_traded.get(sym, 0) + 1
        if side == "buy":
            buy_count += 1
        elif side == "sell":
            sell_count += 1
        total_volume += float(cost) if cost else 0

    top_symbols = sorted(symbols_traded.items(), key=lambda x: x[1], reverse=True)[:5]

    # LLM 분석 시도
    llm_analysis = ""
    if total_trades > 0:
        trade_summary = (
            f"거래소: {', '.join(c.exchange for c in connections)}\n"
            f"30일간 총 {total_trades}건 거래\n"
            f"매수 {buy_count}건 / 매도 {sell_count}건\n"
            f"총 거래대금: {total_volume:,.0f}\n"
            f"거래 종목: {', '.join(s for s, _ in top_symbols)}\n"
            f"현재 보유: {', '.join(holding_coins) if holding_coins else '없음'}\n"
        )
        try:
            resp = await llm_client.chat(
                system=(
                    "너는 FORKER, 유저의 투자 분신이야. "
                    "아래 30일 매매 데이터를 분석해서 간결한 한국어 리포트를 작성해. "
                    "발견한 패턴, 매매 습관, 주의할 점을 포함해. "
                    "이모지를 적절히 써서 읽기 좋게. 3~5줄로 짧게."
                ),
                messages=[{"role": "user", "content": trade_summary}],
                max_tokens=400,
            )
            llm_analysis = resp.text.strip()
        except Exception:
            logger.warning("온보딩 LLM 분석 실패, 기본 통계로 대체")

    # 리포트 조합
    lines = ["📊 너의 30일 매매 분석 리포트\n"]

    if total_trades == 0:
        lines.append("최근 30일간 매매 내역이 없어.")
        if holding_coins:
            lines.append(f"현재 보유 중: {', '.join(holding_coins)}")
        lines.append("대화하면서 점점 배워갈게!")
    else:
        lines.append(
            f"· 총 {total_trades}건 (매수 {buy_count} / 매도 {sell_count})"
        )
        if top_symbols:
            lines.append(
                f"· 주 종목: {', '.join(s for s, _ in top_symbols)}"
            )
        if holding_coins:
            lines.append(f"· 현재 보유: {', '.join(holding_coins)}")
        if total_volume > 0:
            lines.append(f"· 총 거래대금: {total_volume:,.0f}")

        if llm_analysis:
            lines.append(f"\n{llm_analysis}")

    lines.append(
        "\n이제 너의 투자 스타일과 지키는 원칙을 자유롭게 알려줘!\n"
        "한 번에 다 말해도 돼.\n\n"
        "예시: '선물 위주로 하고 펀딩비 보고 들어가. 주로 SOL ETH. "
        "손절 -5%, 한 종목 30% 이상 안 넣어.'"
    )

    return "\n".join(lines)


async def handle_style_input(
    session: AsyncSession,
    user: User,
    text: str,
) -> str:
    """스타일 + 원칙 자유 입력 → LLM이 자동 분리 → DB 저장."""
    user.style_raw = text

    # LLM으로 스타일/원칙 파싱
    style_parsed: dict[str, Any] = {}
    principles_list: list[str] = []

    try:
        resp = await llm_client.chat(
            system=(
                "유저의 투자 스타일 설명을 분석해서 JSON으로 반환해.\n"
                "반드시 아래 형식의 JSON만 출력해. 다른 텍스트 금지.\n\n"
                '{"style": {"type": "...", "entry_signal": "...", '
                '"preferred_symbols": [...]}, '
                '"principles": ["원칙1", "원칙2", ...]}'
            ),
            messages=[{"role": "user", "content": text}],
            max_tokens=500,
        )
        parsed = json.loads(resp.text.strip())
        style_parsed = parsed.get("style", {})
        principles_list = parsed.get("principles", [])
    except Exception:
        logger.warning("스타일 파싱 LLM 실패, 원문 그대로 저장")
        # LLM 실패 시 간단 파싱: 쉼표/줄바꿈으로 분리
        for chunk in text.replace("\n", ",").split(","):
            chunk = chunk.strip()
            if chunk:
                principles_list.append(chunk)

    user.style_parsed = style_parsed
    await session.flush()

    # 원칙 DB 저장
    for content in principles_list:
        session.add(
            Principle(
                user_id=user.id,
                content=content,
                source="user_input",
            )
        )
    await session.flush()

    # 온보딩 완료
    user.onboarding_step = 4
    await session.flush()

    # 싱크로율 계산
    sync_data = await calculate_sync_rate(session, user)
    sync_text = format_sync_rate(sync_data)

    result = (
        "파악했어!\n"
        f"{sync_text}\n\n"
        "이제 시장을 같이 볼 준비 됐어. 궁금한 거 물어보거나, 시그널이 오면 피드백해줘!\n\n"
        "💡 내가 할 수 있는 것:\n"
        "· 시장 질문 → 'VANA 왜 올라?'\n"
        "· 실시간 알림 → 'BTC 10만 되면 알려줘'\n"
        "· 실시간 감시 → '업비트 거래량 상위 3개가 BTC보다 높으면 알려줘'\n"
        "· 브리핑 요청 → '거래대금 터지면 분석해줘'\n"
        "· 차트 분석 → 차트 캡처📸 보내면 분석\n"
        "· 투자 원칙 → /principles (추가/수정/삭제 자유)\n"
        "· 싱크로율 → /sync"
    )
    return result


async def handle_principles_edit(
    session: AsyncSession,
    user: User,
    text: str,
) -> str:
    """투자 원칙 편집 — LLM이 의도(추가/수정/삭제/전체교체) 자동 분류."""
    from sqlalchemy import select

    # 현재 활성 원칙 조회
    result = await session.execute(
        select(Principle).where(
            Principle.user_id == user.id, Principle.is_active.is_(True)
        )
    )
    current = result.scalars().all()
    current_text = "\n".join(f"{i+1}. {p.content}" for i, p in enumerate(current))

    try:
        resp = await llm_client.chat(
            system=(
                "유저의 투자 원칙 편집 요청을 분석해서 JSON으로 반환해.\n"
                "반드시 아래 형식의 JSON만 출력해. 다른 텍스트 금지.\n\n"
                '{"action": "add|modify|delete|replace_all", '
                '"details": ...}\n\n'
                "action별 details:\n"
                '- add: {"new_principles": ["원칙1", ...]}\n'
                '- modify: {"index": 1, "new_content": "수정된 원칙"}\n'
                '- delete: {"index": 1}\n'
                '- replace_all: {"new_principles": ["원칙1", ...]}\n\n'
                "index는 1부터 시작.\n\n"
                f"현재 원칙:\n{current_text or '(없음)'}"
            ),
            messages=[{"role": "user", "content": text}],
            max_tokens=500,
        )
        parsed = json.loads(resp.text.strip())
        action = parsed.get("action", "add")
        details = parsed.get("details", {})
    except Exception:
        logger.warning("원칙 편집 LLM 실패, 추가로 처리")
        action = "add"
        details = {"new_principles": [text.strip()]}

    if action == "add":
        for p_text in details.get("new_principles", [text.strip()]):
            session.add(
                Principle(user_id=user.id, content=p_text, source="user_input")
            )
        await session.flush()
        msg = "✅ 추가했어!"

    elif action == "modify":
        idx = details.get("index", 1) - 1
        if 0 <= idx < len(current):
            current[idx].content = details.get("new_content", current[idx].content)
            await session.flush()
            msg = f"✅ 수정했어! {idx + 1}. {current[idx].content}"
        else:
            msg = "해당 번호의 원칙을 찾을 수 없어."

    elif action == "delete":
        idx = details.get("index", 1) - 1
        if 0 <= idx < len(current):
            current[idx].is_active = False
            await session.flush()
            msg = "✅ 삭제했어!"
        else:
            msg = "해당 번호의 원칙을 찾을 수 없어."

    elif action == "replace_all":
        for p in current:
            p.is_active = False
        for p_text in details.get("new_principles", []):
            session.add(
                Principle(user_id=user.id, content=p_text, source="user_input")
            )
        await session.flush()
        msg = "✅ 전체 교체했어!"

    else:
        msg = "요청을 이해하지 못했어. 다시 말해줄래?"

    # 현재 원칙 목록 보여주기
    result2 = await session.execute(
        select(Principle).where(
            Principle.user_id == user.id, Principle.is_active.is_(True)
        )
    )
    updated = result2.scalars().all()
    if updated:
        principle_list = "\n".join(f"{i+1}. {p.content}" for i, p in enumerate(updated))
        msg += f"\n\n📋 현재 원칙:\n{principle_list}"

    return msg
