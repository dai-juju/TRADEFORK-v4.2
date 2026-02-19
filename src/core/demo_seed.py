"""데모 모드 — /start demo 시 현실적 더미 데이터 삽입.

기존 유저 데이터가 있으면 전부 삭제 후 재삽입.
Pinecone upsert는 스킵 — embedding_text만 DB에 저장.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    BaseStream,
    ChatMessage,
    Episode,
    ExchangeConnection,
    Principle,
    Signal,
    Trade,
    User,
    UserTrigger,
)
from src.security.encryption import encrypt

logger = logging.getLogger(__name__)

_NOW = None  # lazy init


def _now() -> datetime:
    global _NOW
    if _NOW is None:
        _NOW = datetime.now(timezone.utc)
    return _NOW


def _days_ago(days: float, jitter_hours: float = 0) -> datetime:
    """days일 전 시각. jitter_hours만큼 랜덤 분산."""
    offset = timedelta(days=days)
    if jitter_hours:
        offset += timedelta(hours=random.uniform(-jitter_hours, jitter_hours))
    return _now() - offset


# ------------------------------------------------------------------
# 메인 시드 함수
# ------------------------------------------------------------------


async def seed_demo_data(
    session: AsyncSession,
    user: User,
) -> dict[str, Any]:
    """유저에게 현실적 더미 데이터를 삽입.

    Returns:
        삽입 통계 dict (trades, episodes, signals 등 카운트)
    """
    uid = user.id

    # 1) 기존 데이터 전부 삭제 (CASCADE 대상 아닌 것도 명시)
    for model in [
        ChatMessage, Signal, Episode, Trade, UserTrigger,
        BaseStream, Principle, ExchangeConnection,
    ]:
        await session.execute(delete(model).where(model.user_id == uid))
    await session.flush()

    # 2) User 프로필 업데이트
    user.onboarding_step = 4
    user.tier = "pro"
    user.language = "ko"
    user.style_raw = "선물 위주, 펀딩비 중심 매매. SOL ETH 주력. 손절 -5%"
    user.style_parsed = {
        "type": "futures_main",
        "entry_signal": "funding_rate",
        "preferred_symbols": ["SOL", "ETH"],
        "avg_hold_hours": 6.5,
        "risk_level": "moderate",
    }
    user.briefing_hour = 8
    user.daily_signal_count = 0
    user.is_active = True
    user.last_active_at = _now()
    await session.flush()

    # 3) ExchangeConnection — 바이낸스 더미
    session.add(ExchangeConnection(
        user_id=uid,
        exchange="binance",
        api_key_encrypted=encrypt("DEMO_API_KEY_NOT_REAL"),
        api_secret_encrypted=encrypt("DEMO_SECRET_NOT_REAL"),
        is_active=True,
        last_checked_at=_now(),
    ))
    await session.flush()

    # 4) Principles 5개
    principles_texts = [
        "손절 -5% 엄수",
        "한 종목에 포트폴리오 30% 이상 안 넣어",
        "펀딩비 -0.1% 이하면 롱 검토",
        "레버리지 최대 10배",
        "급등 추격매수 금지 — 눌림목 확인 후 진입",
    ]
    for text in principles_texts:
        session.add(Principle(
            user_id=uid, content=text, source="user_input", is_active=True,
        ))
    await session.flush()

    # 5) Trades 15개 — 10승 5패 (67%), SOL 7 / ETH 5 / DOGE 3
    trades = _build_trades(uid)
    for t in trades:
        session.add(t)
    await session.flush()

    # 6) Episodes 10개
    episodes = _build_episodes(uid)
    for ep in episodes:
        session.add(ep)
    await session.flush()

    # 7) Signals 3개
    signals = _build_signals(uid)
    for sig in signals:
        session.add(sig)
    await session.flush()

    # 8) BaseStream — BTC/ETH/SOL/DOGE Hot + 기본 프리셋
    base_streams = _build_base_streams(uid)
    for bs in base_streams:
        session.add(bs)
    await session.flush()

    # 9) UserTrigger 2개
    triggers = _build_triggers(uid)
    for tr in triggers:
        session.add(tr)
    await session.flush()

    # 10) ChatMessage 최근 대화 10개
    messages = _build_chat_messages(uid)
    for msg in messages:
        session.add(msg)
    await session.flush()

    stats = {
        "trades": len(trades),
        "episodes": len(episodes),
        "signals": len(signals),
        "principles": len(principles_texts),
        "triggers": len(triggers),
        "base_streams": len(base_streams),
        "chat_messages": len(messages),
    }
    logger.info("데모 시드 완료: user=%s, stats=%s", user.telegram_id, stats)
    return stats


# ------------------------------------------------------------------
# Trades 생성 — 15건, 10승 5패
# ------------------------------------------------------------------

_TRADE_SPECS: list[dict[str, Any]] = [
    # SOL 7건 (5승 2패)
    {"symbol": "SOL/USDT", "side": "long", "entry": 168.5, "exit": 188.7, "pnl": 12.0, "lev": 5, "days": 28, "reason": "펀딩비 -0.15% 확인. SOL 일봉 지지선 반등 구간. 거래대금 증가 추세."},
    {"symbol": "SOL/USDT", "side": "long", "entry": 192.3, "exit": 207.7, "pnl": 8.0, "lev": 3, "days": 22, "reason": "4h 볼밴 하단 터치 + RSI 30 부근. 펀딩비 중립, 평소 이 구간에서 반등."},
    {"symbol": "SOL/USDT", "side": "long", "entry": 185.0, "exit": 175.7, "pnl": -5.0, "lev": 5, "days": 18, "reason": "펀딩비 -0.08%로 약한 매수 시그널. 근데 BTC 하방 압력에 동반 하락."},
    {"symbol": "SOL/USDT", "side": "long", "entry": 172.0, "exit": 189.9, "pnl": 10.4, "lev": 5, "days": 14, "reason": "V자 반등 초입. 펀딩비 -0.2% 과매도 구간. 전형적인 숏 스퀴즈 패턴."},
    {"symbol": "SOL/USDT", "side": "short", "entry": 210.5, "exit": 199.0, "pnl": 5.5, "lev": 3, "days": 10, "reason": "신고가 부근 숏. 펀딩비 +0.05% 과열. 일봉 RSI 78."},
    {"symbol": "SOL/USDT", "side": "long", "entry": 195.0, "exit": 215.8, "pnl": 10.7, "lev": 5, "days": 5, "reason": "4h 상승 채널 하단 지지. 펀딩비 -0.12%. OI 증가 + 거래대금 폭증."},
    {"symbol": "SOL/USDT", "side": "long", "entry": 205.0, "exit": 196.9, "pnl": -3.9, "lev": 5, "days": 2, "reason": "전고점 돌파 기대했는데 이중천장 형성. 손절."},
    # ETH 5건 (3승 2패)
    {"symbol": "ETH/USDT", "side": "short", "entry": 3850.0, "exit": 3965.5, "pnl": -3.0, "lev": 3, "days": 26, "reason": "ETF 유입 과열 구간 숏. 근데 연속 유입으로 추가 상승."},
    {"symbol": "ETH/USDT", "side": "long", "entry": 3420.0, "exit": 3933.0, "pnl": 15.0, "lev": 3, "days": 20, "reason": "주봉 지지선 + 펀딩비 -0.1%. ETH/BTC 비율 반등 시작. 대형 매집 온체인 포착."},
    {"symbol": "ETH/USDT", "side": "long", "entry": 3700.0, "exit": 3886.0, "pnl": 5.0, "lev": 2, "days": 15, "reason": "BTC 횡보 구간에서 ETH 독자 상승 패턴. 펀딩비 중립."},
    {"symbol": "ETH/USDT", "side": "long", "entry": 3950.0, "exit": 4108.0, "pnl": 8.0, "lev": 5, "days": 8, "reason": "Dencun 업그레이드 기대감. L2 TVL 급증. 펀딩비 -0.05%."},
    {"symbol": "ETH/USDT", "side": "long", "entry": 4050.0, "exit": 3847.5, "pnl": -5.0, "lev": 5, "days": 3, "reason": "전고점 돌파 실패 후 급락. BTC 조정 동반. 원칙대로 -5% 손절."},
    # DOGE 3건 (2승 1패)
    {"symbol": "DOGE/USDT", "side": "long", "entry": 0.320, "exit": 0.353, "pnl": 10.3, "lev": 10, "days": 24, "reason": "일론 트윗 직후 진입. DOGE 거래대금 300% 폭증. 펀딩비 아직 -0.02%."},
    {"symbol": "DOGE/USDT", "side": "long", "entry": 0.345, "exit": 0.381, "pnl": 10.4, "lev": 10, "days": 12, "reason": "밈코인 랠리 시작. DOGE 4h 골든크로스. 업비트 거래대금 1위."},
    {"symbol": "DOGE/USDT", "side": "long", "entry": 0.370, "exit": 0.352, "pnl": -4.9, "lev": 10, "days": 6, "reason": "추가 상승 기대했으나 밈코인 전체 조정. 연속 수익 후 과신."},
]


def _build_trades(uid: int) -> list[Trade]:
    trades: list[Trade] = []
    for spec in _TRADE_SPECS:
        opened = _days_ago(spec["days"], jitter_hours=8)
        hold_hours = random.uniform(2, 24)
        closed = opened + timedelta(hours=hold_hours)
        size = round(random.uniform(500, 3000), 2)
        pnl_amount = round(size * spec["pnl"] / 100, 2)

        trades.append(Trade(
            user_id=uid,
            exchange="binance",
            symbol=spec["symbol"],
            side=spec["side"],
            entry_price=spec["entry"],
            exit_price=spec["exit"],
            size=size,
            leverage=spec["lev"],
            pnl_percent=spec["pnl"],
            pnl_amount=pnl_amount,
            status="closed",
            forker_reasoning=spec["reason"],
            user_confirmed_reasoning=random.choice([True, True, True, None]),
            opened_at=opened,
            closed_at=closed,
        ))
    return trades


# ------------------------------------------------------------------
# Episodes 생성 — 10개
# ------------------------------------------------------------------


def _build_episodes(uid: int) -> list[Episode]:
    episodes: list[Episode] = []

    # trade 5개
    trade_episodes = [
        {
            "days": 28, "action": "SOL/USDT 롱 +12%",
            "market": {"btc": 97500, "sol": 168.5, "funding_sol": -0.0015, "fear_greed": 45},
            "reasoning": "펀딩비 -0.15% 확인 후 진입. 일봉 지지선 반등. 예상대로 숏커버 랠리 발생.",
            "result": {"pnl_percent": 12.0, "direction_match": True, "result": "적중"},
            "embed": "SOL 롱 +12% | 펀딩비 -0.15% 진입 | 숏커버 랠리 | 일봉 지지선 반등",
        },
        {
            "days": 26, "action": "ETH/USDT 숏 -3%",
            "market": {"btc": 102000, "eth": 3850, "funding_eth": 0.02, "fear_greed": 72},
            "reasoning": "ETF 유입 과열 구간 숏 진입. 근데 연속 유입으로 추가 상승. 판단 오류.",
            "result": {"pnl_percent": -3.0, "direction_match": True, "result": "미스"},
            "embed": "ETH 숏 -3% | ETF 과열 숏 | 연속 유입으로 추가 상승 | 판단 오류",
        },
        {
            "days": 20, "action": "ETH/USDT 롱 +15%",
            "market": {"btc": 98000, "eth": 3420, "funding_eth": -0.001, "fear_greed": 38},
            "reasoning": "주봉 지지선 + 펀딩비 음수. ETH/BTC 비율 반등 시작. 대형 매집 온체인 포착.",
            "result": {"pnl_percent": 15.0, "direction_match": True, "result": "적중"},
            "embed": "ETH 롱 +15% | 주봉 지지선 | 펀딩비 음수 | 온체인 매집 포착",
        },
        {
            "days": 14, "action": "SOL/USDT 롱 +10.4%",
            "market": {"btc": 95000, "sol": 172, "funding_sol": -0.002, "fear_greed": 32},
            "reasoning": "V자 반등 초입. 펀딩비 -0.2% 과매도. 전형적 숏 스퀴즈.",
            "result": {"pnl_percent": 10.4, "direction_match": True, "result": "적중"},
            "embed": "SOL 롱 +10.4% | 펀딩비 -0.2% 과매도 | V자 반등 | 숏 스퀴즈",
        },
        {
            "days": 6, "action": "DOGE/USDT 롱 -4.9%",
            "market": {"btc": 103500, "doge": 0.37, "funding_doge": 0.01, "fear_greed": 68},
            "reasoning": "밈코인 전체 조정. 연속 수익 후 과신으로 리스크 관리 부족. 반성.",
            "result": {"pnl_percent": -4.9, "direction_match": True, "result": "미스"},
            "embed": "DOGE 롱 -4.9% | 밈코인 조정 | 과신 | 리스크 관리 부족",
        },
    ]
    for te in trade_episodes:
        episodes.append(Episode(
            user_id=uid,
            episode_type="trade",
            market_context=te["market"],
            user_action=te["action"],
            trade_data={"symbol": te["action"].split()[0], "pnl": te["result"]["pnl_percent"]},
            reasoning=te["reasoning"],
            trade_result=te["result"],
            embedding_text=te["embed"],
            created_at=_days_ago(te["days"], jitter_hours=4),
        ))

    # chat 2개
    episodes.append(Episode(
        user_id=uid,
        episode_type="chat",
        market_context={"btc": 101000, "fear_greed": 55},
        user_action="VANA 왜 올라?",
        reasoning="유저가 VANA 급등 이유 질문. AI 에이전트 섹터 테마 랠리 + 거래대금 500% 증가 설명.",
        embedding_text="VANA 급등 이유 질문 | AI 에이전트 테마 | 거래대금 폭증",
        created_at=_days_ago(16, jitter_hours=6),
    ))
    episodes.append(Episode(
        user_id=uid,
        episode_type="chat",
        market_context={"btc": 99500, "fear_greed": 42},
        user_action="시장 분위기 어때?",
        reasoning="유저가 전반적 시장 분위기 질문. Fear&Greed 42, BTC 횡보, 알트 순환매 진행 중 설명.",
        embedding_text="시장 분위기 질문 | Fear&Greed 42 | BTC 횡보 | 알트 순환매",
        created_at=_days_ago(11, jitter_hours=6),
    ))

    # feedback 2개
    episodes.append(Episode(
        user_id=uid,
        episode_type="feedback",
        user_action="시그널 피드백 (동의): SOL",
        trade_data={"symbol": "SOL/USDT", "direction": "long", "confidence": 0.78, "user_agreed": True},
        reasoning="SOL 롱 시그널에 동의. 펀딩비 근거에 납득.",
        embedding_text="시그널 동의 | SOL 롱 | 펀딩비 근거 납득",
        created_at=_days_ago(9, jitter_hours=4),
    ))
    episodes.append(Episode(
        user_id=uid,
        episode_type="feedback",
        user_action="시그널 피드백 (반대): ETH",
        trade_data={"symbol": "ETH/USDT", "direction": "short", "confidence": 0.62, "user_agreed": False},
        reasoning="ETH 숏 시그널에 반대. 유저: 'ETF 자금 유입 계속되는데 숏은 좀...'",
        embedding_text="시그널 반대 | ETH 숏 | 유저: ETF 자금 유입 지속 우려",
        created_at=_days_ago(7, jitter_hours=4),
    ))

    # signal 1개
    episodes.append(Episode(
        user_id=uid,
        episode_type="signal",
        market_context={"btc": 103000, "sol": 208, "funding_sol": -0.0018, "fear_greed": 50},
        user_action="시그널: SOL 롱 (Patrol 감지)",
        reasoning="Patrol이 SOL 펀딩비 -0.18% 이상 감지. 유저 패턴상 이 구간에서 롱 진입 확률 높음.",
        embedding_text="SOL 롱 시그널 | Patrol 감지 | 펀딩비 -0.18% | 유저 패턴 매칭",
        created_at=_days_ago(5, jitter_hours=2),
    ))

    return episodes


# ------------------------------------------------------------------
# Signals 생성 — 3개
# ------------------------------------------------------------------


def _build_signals(uid: int) -> list[Signal]:
    return [
        # 1) 동의 + 매매 성공
        Signal(
            user_id=uid,
            signal_type="trade_signal",
            content="SOL 펀딩비 -0.15% 구간. 너처럼 봤을 때 롱 진입 타이밍이야.",
            reasoning="펀딩비 -0.15% + 4h 볼밴 하단 지지. 너 과거 이런 구간에서 평균 +10% 수익.",
            counter_argument="BTC가 횡보 중이라 알트 독자 상승 제한될 수 있어.",
            confidence=0.78,
            confidence_style=0.85,
            confidence_history=0.72,
            confidence_market=0.75,
            symbol="SOL/USDT",
            direction="long",
            stop_loss="$168 (-5%)",
            user_agreed=True,
            trade_followed=True,
            trade_result_pnl=12.0,
            created_at=_days_ago(28, jitter_hours=2),
        ),
        # 2) 동의 + 매매 실패
        Signal(
            user_id=uid,
            signal_type="trade_signal",
            content="DOGE 밈코인 랠리 진행 중. 거래대금 급증. 추가 상승 가능성.",
            reasoning="DOGE 4h 골든크로스 + 업비트 거래대금 1위. 밈코인 전체 상승 흐름.",
            counter_argument="이미 +30% 올랐고, 밈코인 랠리는 갑자기 끝날 수 있어. FOMO 주의.",
            confidence=0.62,
            confidence_style=0.55,
            confidence_history=0.60,
            confidence_market=0.70,
            symbol="DOGE/USDT",
            direction="long",
            stop_loss="$0.35 (-5%)",
            user_agreed=True,
            trade_followed=True,
            trade_result_pnl=-4.9,
            created_at=_days_ago(6, jitter_hours=2),
        ),
        # 3) 미응답
        Signal(
            user_id=uid,
            signal_type="trade_signal",
            content="ETH 전고점 돌파 시도 중. Dencun 업그레이드 기대감 반영.",
            reasoning="ETH $4000 돌파 시도. L2 TVL 급증. 기관 매집 온체인 포착.",
            counter_argument="$4000은 강한 저항. 돌파 실패 시 $3800까지 눌림 가능.",
            confidence=0.68,
            confidence_style=0.70,
            confidence_history=0.65,
            confidence_market=0.68,
            symbol="ETH/USDT",
            direction="long",
            stop_loss="$3800 (-5%)",
            user_agreed=None,
            trade_followed=None,
            created_at=_days_ago(3, jitter_hours=2),
        ),
    ]


# ------------------------------------------------------------------
# BaseStream — Hot 프리셋
# ------------------------------------------------------------------


def _build_base_streams(uid: int) -> list[BaseStream]:
    streams: list[BaseStream] = []
    hot_symbols = [
        ("price", "BTC", {"source": "binance", "pair": "BTCUSDT"}),
        ("price", "ETH", {"source": "binance", "pair": "ETHUSDT"}),
        ("price", "SOL", {"source": "binance", "pair": "SOLUSDT"}),
        ("price", "DOGE", {"source": "binance", "pair": "DOGEUSDT"}),
        ("funding", "BTC", {"source": "binance", "pair": "BTCUSDT"}),
        ("funding", "ETH", {"source": "binance", "pair": "ETHUSDT"}),
        ("funding", "SOL", {"source": "binance", "pair": "SOLUSDT"}),
        ("indicator", "fear_greed", {"source": "alternative.me"}),
        ("spread", "kimchi", {"source": "upbit_binance", "pair": "BTC"}),
        ("news", None, {"sources": ["cryptopanic", "coinness"]}),
    ]
    for stype, sym, cfg in hot_symbols:
        streams.append(BaseStream(
            user_id=uid,
            stream_type=stype,
            symbol=sym,
            config=cfg,
            temperature="hot",
            last_mentioned_at=_days_ago(random.uniform(0, 3)),
        ))
    return streams


# ------------------------------------------------------------------
# UserTrigger — 2개
# ------------------------------------------------------------------


def _build_triggers(uid: int) -> list[UserTrigger]:
    return [
        UserTrigger(
            user_id=uid,
            trigger_type="alert",
            condition={"type": "funding_below", "symbol": "SOL", "value": -0.001},
            description="SOL 펀딩비 -0.1% 이하 시 알림",
            source="user_request",
            is_active=True,
        ),
        UserTrigger(
            user_id=uid,
            trigger_type="alert",
            condition={"type": "price_above", "symbol": "BTC", "value": 100000},
            description="BTC $100,000 도달 시 알림",
            source="user_request",
            is_active=True,
        ),
    ]


# ------------------------------------------------------------------
# ChatMessage — 최근 대화 10개
# ------------------------------------------------------------------


def _build_chat_messages(uid: int) -> list[ChatMessage]:
    convos: list[tuple[str, str, str, float]] = [
        # (role, content, intent, days_ago)
        ("user", "야 SOL 펀딩비 어때 지금?", "market_question", 4.5),
        ("assistant", "SOL 펀딩비 현재 -0.08%야. 아직 너 기준(-0.1%)까진 안 왔는데 점점 내려가는 추세. 4h 차트 기준 볼밴 하단 근접 중이야.", "market_question", 4.5),
        ("user", "BTC 왜 이렇게 횡보야 짜증나", "general", 3.2),
        ("assistant", "ㅋㅋ 옆으로 기는 거 답답하지. ETF 자금 유입은 꾸준한데 매도 물량이랑 상쇄되는 중이야. 근데 너 스타일이 횡보장에서 알트 순환매 잘 잡잖아. SOL이나 ETH 쪽 기회 노려보는 건?", "general", 3.2),
        ("user", "맞아 SOL 눌리면 들어갈 생각", "general", 3.1),
        ("assistant", "ㅇㅇ 너 평소 패턴대로면 펀딩비 -0.1% 이하 + 4h 볼밴 하단이 네 진입 시그널이야. 알림 설정해둘까?", "general", 3.1),
        ("user", "이미 해놨어 ㅋㅋ", "general", 3.0),
        ("assistant", "ㅋㅋ 역시. 그럼 트리거 발동되면 바로 분석해줄게. 참고로 최근 SOL 비슷한 구간에서 너 평균 +10% 수익이었어.", "general", 3.0),
        ("user", "어제 DOGE 손절한 거 복기해줘", "review", 1.5),
        ("assistant", "DOGE 롱 -4.9%로 마감했지. 진입 근거는 밈코인 랠리 + 거래대금 폭증이었는데, 이미 +30% 오른 시점에서 진입한 게 타이밍이 늦었어. 너 원칙에 '급등 추격매수 금지'가 있잖아. 이번엔 연속 수익에 과신한 것 같아. 다음엔 눌림목 확인하고 들어가자.", "review", 1.5),
    ]
    messages: list[ChatMessage] = []
    for role, content, intent, days in convos:
        messages.append(ChatMessage(
            user_id=uid,
            role=role,
            content=content,
            message_type="text",
            intent=intent,
            created_at=_days_ago(days, jitter_hours=1),
        ))
    return messages
