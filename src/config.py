"""환경변수 로드 + 설정 상수."""

import os
from pathlib import Path

from dotenv import load_dotenv

# .env 파일 로드
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# --- Anthropic (LLM) ---
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_SONNET: str = "claude-sonnet-4-5-20250929"
MODEL_OPUS: str = "claude-opus-4-6"

# --- Pinecone (벡터 DB) ---
PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME: str = os.getenv("PINECONE_INDEX_NAME", "tradefork-episodes")
PINECONE_DIMENSION: int = 1024
PINECONE_METRIC: str = "cosine"

# --- 암호화 ---
ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")

# --- 외부 데이터 ---
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
CMC_API_KEY: str = os.getenv("CMC_API_KEY", "")
CRYPTOPANIC_API_KEY: str = os.getenv("CRYPTOPANIC_API_KEY", "")

# --- 거래소 (개발 테스트용) ---
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")

# --- DB ---
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
REDIS_URL: str = os.getenv("REDIS_URL", "")

# --- Pro 요금제 제한 ---
PRO_PATROL_INTERVAL_SECONDS: int = 3600  # 1시간
PRO_DAILY_SIGNAL_LIMIT: int = 5
PRO_MAX_EXCHANGES: int = 3
PRO_RATE_LIMIT: int = 120  # req/min

# --- Base 온도 관리 ---
HOT_POLL_INTERVAL: int = 10        # 초
WARM_POLL_INTERVAL: int = 1800     # 30분
HOT_THRESHOLD_DAYS: int = 7
WARM_THRESHOLD_DAYS: int = 30

# --- 매매 감지 ---
TRADE_POLL_INTERVAL: int = 30      # 초
DUST_THRESHOLD_PERCENT: float = 1.0  # 잔고의 1% 미만 스킵
