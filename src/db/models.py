"""SQLAlchemy async 모델 — TRADEFORK 전체 테이블."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship

# PostgreSQL → JSONB, 그 외(sqlite 등) → JSON
_JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 Declarative Base."""

    pass


# ===== USERS =====
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    language = Column(String(10), nullable=False, default="ko")
    tier = Column(String(20), nullable=False, default="pro")
    onboarding_step = Column(Integer, nullable=False, default=0)
    style_raw = Column(Text, nullable=True)
    style_parsed = Column(_JsonType, nullable=True)
    daily_signal_count = Column(Integer, nullable=False, default=0)
    daily_signal_reset_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # relationships — passive_deletes=True: DB의 ON DELETE CASCADE에 위임
    exchange_connections = relationship(
        "ExchangeConnection", back_populates="user", lazy="selectin",
        passive_deletes=True,
    )
    episodes = relationship(
        "Episode", back_populates="user", lazy="selectin", passive_deletes=True,
    )
    principles = relationship(
        "Principle", back_populates="user", lazy="selectin", passive_deletes=True,
    )
    trades = relationship(
        "Trade", back_populates="user", lazy="selectin", passive_deletes=True,
    )
    base_streams = relationship(
        "BaseStream", back_populates="user", lazy="selectin", passive_deletes=True,
    )
    user_triggers = relationship(
        "UserTrigger", back_populates="user", lazy="selectin", passive_deletes=True,
    )
    signals = relationship(
        "Signal", back_populates="user", lazy="selectin", passive_deletes=True,
    )
    chat_messages = relationship(
        "ChatMessage", back_populates="user", lazy="selectin", passive_deletes=True,
    )
    patrol_logs = relationship(
        "PatrolLog", back_populates="user", lazy="selectin", passive_deletes=True,
    )


# ===== EXCHANGE CONNECTIONS =====
class ExchangeConnection(Base):
    __tablename__ = "exchange_connections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    exchange = Column(String(50), nullable=False)  # "binance" | "upbit" | "bithumb"
    api_key_encrypted = Column(LargeBinary, nullable=False)
    api_secret_encrypted = Column(LargeBinary, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="exchange_connections")


# ===== EPISODES (Intelligence Module 핵심) =====
class Episode(Base):
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_type = Column(
        String(20), nullable=False
    )  # "trade" | "chat" | "feedback" | "signal" | "patrol"

    # 시장 상황
    market_context = Column(_JsonType, nullable=True)

    # 유저 데이터
    user_action = Column(Text, nullable=False)
    trade_data = Column(_JsonType, nullable=True)
    reasoning = Column(Text, nullable=True)
    trade_result = Column(_JsonType, nullable=True)
    feedback = Column(Text, nullable=True)

    # 캘리브레이션
    expression_calibration = Column(_JsonType, nullable=True)
    style_tags = Column(_JsonType, nullable=True)

    # 벡터 검색용
    pinecone_id = Column(String(255), nullable=True, unique=True)
    embedding_text = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="episodes")
    trades = relationship("Trade", back_populates="episode", lazy="selectin")
    signals = relationship("Signal", back_populates="episode", lazy="selectin")


# ===== INVESTMENT PRINCIPLES (Q3) =====
class Principle(Base):
    __tablename__ = "principles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    source = Column(
        String(30), nullable=False
    )  # "user_input" | "llm_extracted"
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="principles")


# ===== TRADES (Q1 매매 기록) =====
class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exchange = Column(String(50), nullable=False)
    symbol = Column(String(50), nullable=False)  # "SOL/USDT", "BTC/KRW"
    side = Column(String(10), nullable=False)  # "long" | "short" | "buy" | "sell"
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    size = Column(Float, nullable=False)
    leverage = Column(Float, nullable=False, default=1.0)
    pnl_percent = Column(Float, nullable=True)
    pnl_amount = Column(Float, nullable=True)
    status = Column(String(10), nullable=False)  # "open" | "closed"

    # FORKER 추론
    forker_reasoning = Column(Text, nullable=True)
    user_confirmed_reasoning = Column(Boolean, nullable=True)
    user_actual_reasoning = Column(Text, nullable=True)

    episode_id = Column(
        Integer, ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True
    )
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="trades")
    episode = relationship("Episode", back_populates="trades")


# ===== BASE DATA STREAMS (Tier 1 Base) =====
class BaseStream(Base):
    __tablename__ = "base_streams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    stream_type = Column(
        String(30), nullable=False
    )  # "price" | "funding" | "oi" | "news" | "indicator" | "spread"
    symbol = Column(String(30), nullable=True)
    config = Column(_JsonType, nullable=False)
    temperature = Column(String(10), nullable=False, default="hot")  # "hot" | "warm" | "cold"
    last_mentioned_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_value = Column(_JsonType, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="base_streams")


# ===== USER TRIGGERS (Tier 1 User Trigger — 3단계) =====
class UserTrigger(Base):
    __tablename__ = "user_triggers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trigger_type = Column(
        String(20), nullable=False
    )  # "alert" | "signal" | "llm_evaluated"
    condition = Column(_JsonType, nullable=True)
    composite_logic = Column(Text, nullable=True)
    base_streams_needed = Column(_JsonType, nullable=True)
    eval_prompt = Column(Text, nullable=True)
    data_needed = Column(_JsonType, nullable=True)
    description = Column(Text, nullable=False)
    source = Column(
        String(20), nullable=False
    )  # "user_request" | "llm_auto" | "patrol"
    is_active = Column(Boolean, nullable=False, default=True)
    triggered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="user_triggers")


# ===== SIGNALS (시그널 기록) =====
class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    signal_type = Column(String(20), nullable=False)  # "trade_signal" | "briefing"
    content = Column(Text, nullable=False)
    reasoning = Column(Text, nullable=False)
    counter_argument = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False)
    symbol = Column(String(30), nullable=True)
    direction = Column(
        String(10), nullable=True
    )  # "long" | "short" | "exit" | "watch"
    stop_loss = Column(String(100), nullable=True)

    # 피드백
    user_feedback = Column(Text, nullable=True)
    user_agreed = Column(Boolean, nullable=True)
    trade_followed = Column(Boolean, nullable=True)
    trade_result_pnl = Column(Float, nullable=True)

    chart_path = Column(String(500), nullable=True)
    episode_id = Column(
        Integer, ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="signals")
    episode = relationship("Episode", back_populates="signals")


# ===== CHAT HISTORY =====
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(20), nullable=False)  # "user" | "assistant" | "system"
    content = Column(Text, nullable=False)
    message_type = Column(
        String(20), nullable=False, default="text"
    )  # "text" | "image" | "chart"
    intent = Column(
        String(30), nullable=True
    )  # "alert" | "signal_trigger" | "market_question" | "general" | "review"
    metadata_ = Column("metadata", _JsonType, nullable=True)
    telegram_message_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="chat_messages")


# ===== PATROL LOGS =====
class PatrolLog(Base):
    __tablename__ = "patrol_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    patrol_type = Column(
        String(30), nullable=False
    )  # "scheduled" | "deferred_request"
    findings = Column(_JsonType, nullable=False)
    actions_taken = Column(_JsonType, nullable=False)
    base_temp_changes = Column(_JsonType, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = relationship("User", back_populates="patrol_logs")


# 복합 인덱스
Index("ix_trades_user_status", Trade.user_id, Trade.status)
Index("ix_episodes_user_type", Episode.user_id, Episode.episode_type)
Index("ix_chat_messages_user_created", ChatMessage.user_id, ChatMessage.created_at)
Index("ix_base_streams_user_temp", BaseStream.user_id, BaseStream.temperature)
Index("ix_user_triggers_user_active", UserTrigger.user_id, UserTrigger.is_active)
