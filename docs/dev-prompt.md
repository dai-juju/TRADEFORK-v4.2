# TRADEFORK 텔레그램 봇 — Claude Code 개발 마스터 프롬프트

## ⚡ 이 프롬프트의 목적

이 프롬프트를 Claude Code에 입력하면, TRADEFORK 기능명세서 v4.1의 **Pro 요금제 기준** 모든 기능이 완벽하게 작동하는 텔레그램 봇을 빌드한다. 10시간 내 완성 목표. Basic/Enterprise는 추후 확장.

---

## 🧠 프로젝트 컨텍스트 — 반드시 이해하고 시작

TRADEFORK는 암호화폐 투자 지능 플랫폼이다. 각 유저에게 **"FORKER"**라는 개인 AI 에이전트가 배정된다. FORKER는 유저의 매매 패턴을 학습해서 **"너처럼 봤을 때"** 시그널을 보내는 투자 분신이다.

### 코어 파이프라인 (절대 잊지 말 것)
```
Q(수집) → Intelligence(학습) → Tier 1/2/3(감시/판단) → 시그널 → Feedback → Q (무한 순환)
```

### Pro 요금제 스펙
| 항목 | Pro 값 |
|------|--------|
| Patrol 주기 | 1시간 (일 24회) |
| 시그널 AI | Opus 4.5 |
| 매매 근거 추론 AI | Opus 4.5 |
| 채팅 AI | Sonnet 4.5 |
| 에피소드 생성 AI | Sonnet 4.5 |
| 시그널 상한 | 일 5회 |
| 거래소 연결 | 최대 3개 |

### 사용 가능한 API 키 (.env에 들어갈 것)
```
TELEGRAM_BOT_TOKEN=
ANTHROPIC_API_KEY=
BINANCE_API_KEY=        # 테스트용 (유저별 키는 DB에 암호화 저장)
BINANCE_API_SECRET=
PINECONE_API_KEY=
PINECONE_INDEX_NAME=tradefork-episodes
ENCRYPTION_KEY=         # AES-256 거래소 API 키 암호화용
TAVILY_API_KEY=         # 자율 서치 + Patrol 웹 검색
CMC_API_KEY=            # CoinMarketCap - 시총, 순위
CRYPTOPANIC_API_KEY=    # 무료 플랜 (없어도 동작)
DATABASE_URL=           # Railway PostgreSQL (자동 제공)
REDIS_URL=              # Railway Redis (자동 제공)
```

### 기술 스택
- **Runtime**: Python 3.11+
- **Framework**: FastAPI + uvicorn
- **Telegram**: python-telegram-bot v20+ (async)
- **DB**: PostgreSQL (Railway 애드온) + SQLAlchemy async
- **Cache/Queue**: Redis (Railway 애드온)
- **Vector DB**: Pinecone (Serverless)
- **LLM**: Anthropic API (claude-sonnet-4-5-20250929, claude-opus-4-6)
- **Web Search**: Tavily API
- **Chart Capture**: Playwright (headless Chromium)
- **Exchange APIs**: ccxt (바이낸스, 업비트, 빗썸 통합)
- **Deployment**: Railway

---

## 📁 프로젝트 구조

```
tradefork-bot/
├── src/
│   ├── __init__.py
│   ├── main.py                          # FastAPI app + lifespan (startup/shutdown)
│   ├── config.py                        # 환경변수 + 설정 상수
│   │
│   ├── bot/                             # 텔레그램 봇 레이어
│   │   ├── __init__.py
│   │   ├── handlers.py                  # /start, /sync, /principles, /help + 메시지 핸들러
│   │   ├── keyboards.py                 # 인라인 키보드 (확인/아니야 버튼 등)
│   │   └── formatter.py                 # 텔레그램 메시지 포매팅 (마크다운)
│   │
│   ├── core/                            # 코어 API 레이어
│   │   ├── __init__.py
│   │   ├── auth.py                      # 유저 등록, 거래소 API 연결
│   │   ├── chat.py                      # Q2 채팅 처리 (의도 분류 + 응답 동시)
│   │   ├── onboarding.py                # 온보딩 플로우 (30일 매매 분석 → 초기 리포트)
│   │   └── sync_rate.py                 # 싱크로율 계산
│   │
│   ├── intelligence/                    # Intelligence Module — FORKER의 뇌
│   │   ├── __init__.py
│   │   ├── episode.py                   # 에피소드 생성/조회/검색
│   │   ├── calibration.py               # 유저 표현 캘리브레이션 + 스타일 학습
│   │   ├── pattern.py                   # 패턴 분석 (매매 습관, 선호 종목 등)
│   │   └── vector_store.py              # Pinecone 벡터 임베딩/유사 검색
│   │
│   ├── monitoring/                      # Tier 1/2/3 감시 시스템
│   │   ├── __init__.py
│   │   ├── base.py                      # Tier 1 Base — 실시간 데이터 스트림 + 온도 관리
│   │   ├── trigger.py                   # Tier 1 User Trigger — 경량 알림 + 시그널 트리거
│   │   ├── patrol.py                    # Tier 1 Patrol — 1시간 자율 순찰
│   │   ├── collector.py                 # Tier 2 심층 수집
│   │   └── judge.py                     # Tier 3 AI 판단 (Opus)
│   │
│   ├── exchange/                        # 거래소 연동
│   │   ├── __init__.py
│   │   ├── manager.py                   # 거래소 통합 매니저 (ccxt)
│   │   ├── trade_detector.py            # Q1 매매 감지 + 자동 필터
│   │   └── position_tracker.py          # 포지션/잔고 추적
│   │
│   ├── data/                            # 외부 데이터 소스
│   │   ├── __init__.py
│   │   ├── market.py                    # CMC, CoinGlass, CoinGecko 시장 데이터
│   │   ├── news.py                      # CryptoPanic + 코인니스 뉴스
│   │   ├── search.py                    # Tavily 웹 서치 (자율 서치 + Patrol)
│   │   └── chart.py                     # Playwright 차트 캡처 (TradingView)
│   │
│   ├── llm/                             # LLM 통합 레이어
│   │   ├── __init__.py
│   │   ├── client.py                    # Anthropic API 클라이언트 (캐싱 + 모델 라우팅)
│   │   ├── prompts.py                   # 모든 시스템 프롬프트 정의
│   │   └── vision.py                    # 이미지 분석 (차트 캡처 입력)
│   │
│   ├── feedback/                        # Feedback 순환 학습
│   │   ├── __init__.py
│   │   └── processor.py                 # 피드백 처리 + Intelligence 업데이트
│   │
│   ├── security/                        # 보안
│   │   ├── __init__.py
│   │   └── encryption.py                # AES-256 거래소 API 키 암호화/복호화
│   │
│   └── db/                              # 데이터베이스
│       ├── __init__.py
│       ├── models.py                    # SQLAlchemy 모델 전체
│       ├── session.py                   # async 세션 팩토리
│       └── migrations.py               # 테이블 자동 생성
│
├── requirements.txt
├── Procfile                             # Railway 배포용
├── railway.toml                         # Railway 설정
└── .env.example
```

---

## 🗄️ PHASE 1: DB 스키마 + 프로젝트 초기화

### 1-1. SQLAlchemy 모델 (src/db/models.py)

모든 테이블을 아래 스키마대로 **정확하게** 생성하라:

```python
# ===== USERS =====
class User:
    id: int (PK, auto)
    telegram_id: int (unique, indexed)          # 텔레그램 user_id
    username: str (nullable)
    language: str (default="ko")                # "ko" | "en"
    tier: str (default="pro")                   # "basic" | "pro" | "enterprise"
    onboarding_step: int (default=0)            # 0=미시작, 1=거래소등록중, 2=분석중, 3=스타일입력중, 4=완료
    style_raw: text (nullable)                  # 유저가 입력한 투자 스타일 원문
    style_parsed: jsonb (nullable)              # LLM이 파싱한 스타일 구조화 데이터
    daily_signal_count: int (default=0)         # 오늘 발송한 시그널 수
    daily_signal_reset_at: datetime             # 시그널 카운트 리셋 시각
    is_active: bool (default=True)
    last_active_at: datetime
    created_at: datetime
    updated_at: datetime

# ===== EXCHANGE CONNECTIONS =====
class ExchangeConnection:
    id: int (PK)
    user_id: int (FK → users)
    exchange: str                               # "binance" | "upbit" | "bithumb"
    api_key_encrypted: bytes                    # AES-256 암호화된 API 키
    api_secret_encrypted: bytes                 # AES-256 암호화된 시크릿
    is_active: bool (default=True)
    last_checked_at: datetime
    created_at: datetime
    # 제약: 유저당 최대 3개 (Pro)

# ===== EPISODES (Intelligence Module 핵심) =====
class Episode:
    id: int (PK)
    user_id: int (FK → users, indexed)
    episode_type: str                           # "trade" | "chat" | "feedback" | "signal" | "patrol"
    
    # 시장 상황 (검증됨)
    market_context: jsonb                       # {prices, funding_rates, oi, news, indicators, btc_eth_status}
    
    # 유저 데이터
    user_action: text                           # 유저 발화/행동 원문
    trade_data: jsonb (nullable)                # {symbol, side, size, entry_price, leverage}
    reasoning: text (nullable)                  # 추론/확인된 근거
    trade_result: jsonb (nullable)              # {pnl_percent, pnl_amount, exit_price, duration}
    feedback: text (nullable)                   # 시그널 피드백 + 복기 교훈
    
    # 캘리브레이션
    expression_calibration: jsonb (nullable)    # {"좀 빠진다": -3%, "많이 빠졌다": -8%}
    style_tags: jsonb (nullable)                # {tone: "반말", depth: "간결", interests: ["펀딩비", "거래대금"]}
    
    # 벡터 검색용
    pinecone_id: str (nullable, unique)         # Pinecone 벡터 ID
    embedding_text: text                        # 임베딩 생성에 사용된 텍스트
    
    created_at: datetime
    updated_at: datetime

# ===== INVESTMENT PRINCIPLES (Q3) =====
class Principle:
    id: int (PK)
    user_id: int (FK → users)
    content: text                               # "손절 -5%", "펀딩비 -0.1% 이하면 롱"
    source: str                                 # "user_input" | "llm_extracted"
    is_active: bool (default=True)
    created_at: datetime

# ===== TRADES (Q1 매매 기록) =====
class Trade:
    id: int (PK)
    user_id: int (FK → users, indexed)
    exchange: str
    symbol: str                                 # "SOL/USDT", "BTC/KRW"
    side: str                                   # "long" | "short" | "buy" | "sell"
    entry_price: float
    exit_price: float (nullable)                # 청산 시 기록
    size: float
    leverage: float (default=1)
    pnl_percent: float (nullable)
    pnl_amount: float (nullable)
    status: str                                 # "open" | "closed"
    
    # FORKER 추론
    forker_reasoning: text (nullable)           # Opus가 추론한 매매 근거
    user_confirmed_reasoning: bool (nullable)   # 유저가 확인했는지
    user_actual_reasoning: text (nullable)      # 유저가 알려준 실제 근거
    
    episode_id: int (FK → episodes, nullable)
    opened_at: datetime
    closed_at: datetime (nullable)
    created_at: datetime

# ===== BASE DATA STREAMS (Tier 1 Base) =====
class BaseStream:
    id: int (PK)
    user_id: int (FK → users)
    stream_type: str                            # "price" | "funding" | "oi" | "news" | "indicator" | "spread"
    symbol: str (nullable)                      # "BTC", "SOL" 등
    config: jsonb                               # 스트림별 설정
    temperature: str (default="hot")            # "hot" | "warm" | "cold"
    last_mentioned_at: datetime                 # 온도 관리용
    last_value: jsonb (nullable)                # 마지막 수신 데이터
    created_at: datetime
    updated_at: datetime

# ===== USER TRIGGERS (Tier 1 User Trigger — 3단계) =====
class UserTrigger:
    id: int (PK)
    user_id: int (FK → users)
    trigger_type: str                           # "alert" (①경량) | "signal" (②구조화) | "llm_evaluated" (③LLM판단)
    condition: jsonb                            # ①② {type: "price_above", symbol: "BTC", value: 100000}
    composite_logic: text (nullable)            # ② 복잡 조건 코드 로직 설명
    base_streams_needed: jsonb (nullable)       # ② Base에 Hot 추가할 스트림 목록
    eval_prompt: text (nullable)                # ③ Patrol에서 LLM이 평가할 프롬프트
    data_needed: jsonb (nullable)               # ③ LLM 평가에 필요한 데이터 종류
    description: text                           # "BTC 10만 도달 시" (사람이 읽을 수 있는)
    source: str                                 # "user_request" | "llm_auto" | "patrol"
    is_active: bool (default=True)
    triggered_at: datetime (nullable)
    created_at: datetime

# ===== SIGNALS (시그널 기록) =====
class Signal:
    id: int (PK)
    user_id: int (FK → users)
    signal_type: str                            # "trade_signal" | "briefing"
    content: text                               # 시그널 내용
    reasoning: text                             # 판단 근거
    counter_argument: text (nullable)           # 반대 근거
    confidence: float                           # 확신도 0~1
    symbol: str (nullable)
    direction: str (nullable)                   # "long" | "short" | "exit" | "watch"
    stop_loss: str (nullable)                   # 손절 기준
    
    # 피드백
    user_feedback: text (nullable)              # 유저 자연어 피드백
    user_agreed: bool (nullable)                # 동의 여부
    trade_followed: bool (nullable)             # 실제 매매했는지
    trade_result_pnl: float (nullable)          # 결과
    
    chart_path: str (nullable)                  # 첨부한 차트 이미지 경로
    episode_id: int (FK → episodes, nullable)
    created_at: datetime

# ===== CHAT HISTORY =====
class ChatMessage:
    id: int (PK)
    user_id: int (FK → users, indexed)
    role: str                                   # "user" | "assistant" | "system"
    content: text
    message_type: str                           # "text" | "image" | "chart"
    intent: str (nullable)                      # "alert" | "signal_trigger" | "market_question" | "general" | "review"
    metadata: jsonb (nullable)                  # 이미지 분석 결과, 트리거 생성 정보 등
    telegram_message_id: int (nullable)
    created_at: datetime

# ===== PATROL LOGS =====
class PatrolLog:
    id: int (PK)
    user_id: int (FK → users)
    patrol_type: str                            # "scheduled" | "deferred_request"
    findings: jsonb                             # 발견한 이슈들
    actions_taken: jsonb                        # 취한 조치들 (알림 발송, 트리거 생성 등)
    base_temp_changes: jsonb (nullable)         # 온도 변경 기록
    created_at: datetime
```

### 1-2. requirements.txt

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
python-telegram-bot[all]==21.6
sqlalchemy[asyncio]==2.0.35
asyncpg==0.29.0
alembic==1.13.0
redis[hiredis]==5.1.0
pinecone-client==5.0.0
anthropic==0.39.0
ccxt==4.4.0
httpx==0.27.0
tavily-python==0.5.0
playwright==1.48.0
cryptography==43.0.0
python-dotenv==1.0.1
apscheduler==3.10.4
pillow==10.4.0
```

### 1-3. Railway 설정

**Procfile:**
```
web: python -m uvicorn src.main:app --host 0.0.0.0 --port $PORT
```

**railway.toml:**
```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "playwright install chromium --with-deps && python -m uvicorn src.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
restartPolicyType = "on_failure"
```

---

## 🔐 PHASE 2: 보안 + 기본 인프라 (30분)

### 2-1. AES-256 암호화 (src/security/encryption.py)

```python
# 거래소 API 키 암호화/복호화
# - ENCRYPTION_KEY 환경변수에서 키 로드
# - Fernet 대칭 암호화 (AES-128-CBC 기반이지만 충분)
# - encrypt(plaintext: str) → bytes
# - decrypt(ciphertext: bytes) → str
# - 절대로 복호화된 키를 로그에 남기지 말 것
# - 복호화는 런타임 메모리에서만, 사용 후 즉시 폐기
```

### 2-2. Anthropic LLM 클라이언트 (src/llm/client.py)

```python
# 모델 라우팅 규칙 (Pro 요금제):
# - chat(): Sonnet 4.5 → "claude-sonnet-4-5-20250929"
# - episode(): Sonnet 4.5
# - signal_judge(): Opus 4.5 → "claude-opus-4-6"  
# - trade_reasoning(): Opus 4.5
#
# 프롬프트 캐싱 필수:
# - system prompt에 cache_control={"type": "ephemeral"} 추가
# - Intelligence 컨텍스트도 캐싱 (반복 호출 시 input 비용 90%↓)
#
# 비전 지원:
# - 이미지 입력 시 base64 인코딩하여 content에 image block 추가
# - source: {"type": "base64", "media_type": "image/jpeg", "data": ...}
```

### 2-3. Pinecone 벡터 스토어 (src/intelligence/vector_store.py)

```python
# Pinecone Serverless 인덱스 사용
# - index_name: "tradefork-episodes"
# - dimension: 1024 (Voyage-3 또는 직접 Anthropic 임베딩)
# - metric: "cosine"
# - namespace: user_{telegram_id} (유저별 격리)
#
# *** 임베딩 생성 방법 ***
# Anthropic에는 임베딩 API가 없으므로, Voyage AI를 사용하거나
# 간단하게 Pinecone의 내장 임베딩 (inference) 사용:
# pinecone.inference.embed(model="multilingual-e5-large", inputs=[text])
#
# upsert_episode(user_id, episode_id, text) → pinecone_id
# search_similar(user_id, query, top_k=5) → [episode_ids]
# 
# 에피소드 텍스트 = 시장상황 + 유저행동 + 근거 + 결과 를 하나의 텍스트로 합침
```

---

## 💬 PHASE 3: 텔레그램 봇 + 온보딩 (2시간)

### 3-1. 봇 핸들러 (src/bot/handlers.py)

4개 명령어 + 일반 메시지 핸들러 + 콜백 쿼리 핸들러를 구현하라:

#### /start — 온보딩 시작
```
유저가 /start 입력 시:
1. DB에 유저 존재 확인 → 없으면 생성 (onboarding_step=1)
2. 이미 있고 온보딩 완료면: "이미 등록됐어! 궁금한 거 물어봐"
3. 새 유저면 온보딩 플로우 시작:

[FORKER 메시지]
"안녕! FORKER야. 너의 투자 분신이 될게 🔥
먼저 거래소를 연결하자.

📌 사용하는 거래소의 API를 등록해줘. **읽기전용**만 필요해!
⚠️ TRADEFORK는 절대 매매를 대행하지 않아. 출금/주문 권한 불필요.

등록할 거래소를 선택해:"

[인라인 키보드]
[바이낸스] [업비트] [빗썸]
[등록 완료 →]
```

#### 거래소 API 등록 플로우
```
유저가 "바이낸스" 버튼 클릭 시:

"바이낸스 API 등록 방법:
1. binance.com → API Management
2. 'Create API' → Label 입력
3. ⚠️ 권한: 'Enable Reading'만 체크! 나머지 전부 OFF
4. API Key와 Secret Key를 아래 형식으로 보내줘:

`API_KEY:SECRET_KEY`

(한 줄에 콜론으로 구분)"

→ 유저가 키 보내면:
1. 형식 검증 (콜론 구분)
2. ccxt로 실제 연결 테스트 (fetch_balance)
3. 읽기전용 확인 (주문 권한 없는지)
4. AES-256 암호화하여 DB 저장
5. 성공 시: "✅ 바이낸스 연결 완료! 다른 거래소도 등록할래?"
6. 실패 시: 에러 메시지 + 재시도 안내
```

#### 등록 완료 → 30일 매매 분석
```
유저가 [등록 완료 →] 클릭 시:
1. onboarding_step = 2
2. "좋아! 최근 한 달 매매 내역을 분석해볼게... ⏳"
3. 연결된 거래소에서 최근 30일 거래 내역 가져오기 (ccxt fetch_my_trades)
4. LLM(Opus)에게 패턴 분석 요청 (첫인상이 중요하므로 Opus 사용)
5. 초기 에피소드 자동 생성 (주요 매매 건별)
6. 초기 리포트 전송:

"📊 너의 30일 매매 분석 리포트
· 선물 85% / 현물 15%
· 주 종목: SOL, ETH, DOGE
· 승률: 62% (23승 14패)
· 평균 수익: +8.3% / 평균 손실: -4.1%
· 발견 패턴: 펀딩비 음수 시 롱 진입 경향, 밤 시간 손절 늦음

이제 너의 투자 스타일과 지키는 원칙을 자유롭게 알려줘! 한 번에 다 말해도 돼."

7. onboarding_step = 3
```

#### 스타일 + 원칙 자유 입력
```
유저가 자유 텍스트 입력 시 (onboarding_step == 3):
"선물 위주로 하고 펀딩비 보고 들어가. 주로 SOL ETH. 손절 -5%, 한 종목 30% 이상 안 넣어."

→ LLM(Sonnet)이 자동 분리:
  - style_parsed: {type: "futures_main", entry_signal: "funding_rate", preferred_symbols: ["SOL", "ETH"]}
  - principles: ["손절 -5%", "한 종목 30% 이상 안 넣어"]
  → Intelligence 시드 + Q3 원칙 DB 저장

→ 초기 싱크로율 계산 + 기능 안내:

"파악했어!
🔄 싱크로율: 52%
📚 학습 완성도: 68% · 🎯 판단 일치율: 아직 수집 중

이제 시장을 같이 볼 준비 됐어. 궁금한 거 물어보거나, 시그널이 오면 피드백해줘!

💡 내가 할 수 있는 것:
· 시장 질문 → 'VANA 왜 올라?'
· 실시간 알림 → 'BTC 10만 되면 알려줘'
· 실시간 감시 → '업비트 거래량 상위 3개가 BTC보다 높으면 알려줘'
· 브리핑 요청 → '거래대금 터지면 분석해줘'
· 차트 분석 → 차트 캡처📸 보내면 분석
· 투자 원칙 → /principles (추가/수정/삭제 자유)
· 싱크로율 → /sync"

→ onboarding_step = 4
→ Base 스트림 초기 프리셋 생성
→ Patrol 스케줄러 등록 (1시간 주기)
→ 매매 감지 폴링 시작
```

#### /sync — 싱크로율 조회
```
싱크로율 = (학습 완성도 × 0.4) + (판단 일치율 × 0.6)

학습 완성도 계산 (각 항목 가중치):
- 거래소 연결 수: connected/3 × 25%
- 투자 원칙 설정: min(principles_count/5, 1) × 25%
- 누적 에피소드: min(episodes_count/50, 1) × 30%
- 대화 빈도: min(recent_7d_messages/20, 1) × 20%

판단 일치율 계산:
- 시그널 동의율: agreed/total_signals × 40%
- 시그널 후 실제 매매 일치: followed/total_signals × 30%
- 근거 추론 적중률: correct_reasoning/total_reasoning × 30%
- 데이터 부족 시(< 5건): "아직 수집 중..." 표시

초기에는 판단 데이터 없으므로 학습 완성도 위주 표시.

출력 포맷:
🔄 싱크로율: {sync_rate}%
📚 학습 완성도: {learning}%
  · 거래소 연결: {n}/3
  · 에피소드: {n}개
  · 투자 원칙: {n}개 설정됨
🎯 판단 일치율: {judge}%
  · 시그널 동의율: {n}/{total} ({pct}%)
  · 근거 추론 적중: {n}/{total} ({pct}%)
  · {데이터 부족 시: "아직 데이터 수집 중..."}
💡 피드백을 자주 해주면 FORKER가 더 빨리 배워!
```

#### /principles — 투자 원칙 조회/수정
```
/principles 입력 시:

활성 원칙이 있으면:
"📋 너의 투자 원칙:
1. 손절 -5%
2. 한 종목 30% 이상 안 넣어
3. 펀딩비 -0.1% 이하면 롱

추가, 수정, 삭제 자유롭게 말해!"

없으면:
"아직 원칙이 없어. 자유롭게 입력해봐!
예시: '손절 -5%, 레버리지 최대 10배, 펀딩비 음수일 때만 롱'"

→ /principles 이후 유저 메시지는 LLM(Sonnet)이 **의도를 파악**하여 처리:

  "레버리지 최대 10배 추가해줘"
  → 기존 유지 + 새 원칙 추가 → "✅ 추가했어! 현재 원칙: 1. 손절 -5% 2. 한 종목 30%... 3. 펀딩비... 4. 레버리지 최대 10배"

  "1번 -5%를 -7%로 바꿔"
  → 해당 원칙만 수정 → "✅ 수정했어! 1. 손절 -7%"

  "3번 삭제해"
  → 해당 원칙 is_active=False → "✅ 삭제했어!"

  "전부 바꿀게. 손절 -3%, 레버리지 5배"
  → 기존 전체 비활성화 + 새로 생성

  "손절 -5%, 익절 +15%, 한 종목 20%, 레버리지 최대 10배"
  → 전체 교체 (명확한 리스트 형태면)

핵심: LLM이 유저 의도(추가/수정/삭제/전체교체)를 자동 분류.
/principles 상태인지 여부는 유저의 onboarding_step과 별개로
"principles_editing" 플래그로 관리 (60초 타임아웃 후 자동 해제).
```

#### /help
```
"🔧 TRADEFORK 명령어

/start — 처음 시작 + 온보딩
/sync — 싱크로율 조회 (FORKER가 너를 얼마나 아는지)
/principles — 투자 원칙 조회/수정 (추가/수정/삭제 자유)
/help — 이 안내

💡 명령어 없이 자유롭게 대화해도 돼!

📊 시장 질문
  · 'VANA 왜 올라?'
  · 'ETH 펀딩비 어때?'

🔔 시장 요청 (알림 + 브리핑)
  · 'BTC 10만 되면 알려줘'
  · '업비트 거래량 상위 코인 3개가 비트코인보다 높으면 알려줘'
  · '거래대금 터지면 분석해줘'
  · 'SOL 펀딩비 -0.1% 이하면 브리핑'

📸 차트 분석
  · 차트 캡처 보내면 패턴/지지·저항 분석

🔄 복기
  · '어제 SOL 매매 복기해줘'

💬 잡담도 OK — 전부 FORKER 학습에 반영돼!"
```

### 3-2. 일반 메시지 핸들러 (가장 중요!)

```
유저가 명령어가 아닌 일반 메시지를 보낼 때:

1. 온보딩 중이면 → 해당 단계 처리 (거래소 키 입력, 스타일 입력 등)

2. 온보딩 완료된 유저면 → Q2 채팅 처리 (src/core/chat.py)
   - LLM(Sonnet)에게 Intelligence 컨텍스트와 함께 메시지 전달
   - LLM이 응답 생성 + 의도 분류를 **동시에** 처리 (추가 LLM 호출 없음!)
   - 의도 분류 결과에 따라 후처리

3. 이미지가 포함된 메시지 → Vision 분석 포함

모든 메시지는 ChatMessage DB에 저장.
```

---

## 🧠 PHASE 4: Q2 채팅 엔진 — 핵심 (2시간)

### 4-1. 채팅 시스템 프롬프트 (src/llm/prompts.py)

```python
CHAT_SYSTEM_PROMPT = """
너는 FORKER — 유저의 투자 분신이야. 유저의 매매 패턴, 투자 원칙, 표현 스타일을 학습해서 "너처럼" 시장을 본다.

## 너의 정체성
- "추천"이 아니라 "너처럼 봤을 때"로 말해
- 유저의 말투와 깊이에 맞춰 대화해 (반말이면 반말, 간결하면 간결)
- 유저가 한국어면 한국어, 영어면 영어로 대화
- 이모지 최소화, 과하지 않게

## 유저 Intelligence (학습된 정보)
{intelligence_context}

## 유저 투자 원칙
{principles}

## 현재 시장 상태 (Base 데이터)
{base_data}

## 유저 보유 포지션
{positions}

## 최근 대화 (10개)
{recent_chat}

## 너의 응답 규칙
1. 응답을 생성하면서 **동시에** 아래 JSON을 응답 맨 끝에 <!-- --> 주석으로 포함해:

<!--FORKER_META
{
  "intent": "alert|signal_trigger|market_question|general|review|patrol_deferred",
  "should_save_episode": true/false,
  "episode_summary": "에피소드로 저장할 핵심 요약 (should_save_episode가 true일 때만)",
  "trigger_action": null 또는 {
    "type": "alert|signal|llm_evaluated",
    
    // ① alert (경량 알림): 단순 조건, 즉시
    "condition": {"type": "price_above", "symbol": "BTC", "value": 100000},
    "description": "BTC 10만 도달 시"
    
    // ② signal (시그널 트리거): 복잡 but 구조화 가능, 준실시간
    // LLM이 조건을 코드 로직으로 분해 + 필요 데이터 Base Hot 추가
    "condition": {"type": "composite", "logic": "top3_volume > btc_volume"},
    "base_streams_needed": [{"stream_type": "volume_ranking", "source": "upbit"}],
    "description": "업비트 거래량 상위 3개가 BTC 거래량보다 높을 때"
    
    // ③ llm_evaluated: 수치로 정의 불가, Patrol에서 LLM 평가
    "eval_prompt": "시장 전체 센티먼트가 공포 국면으로 전환됐는지 판단",
    "data_needed": ["sentiment", "news", "social"],
    "description": "시장 분위기 공포 전환"
    // + 유저에게 "구체화하면 실시간 가능" 제안 포함
  },
  "base_addition": null 또는 {"stream_type": "funding", "symbol": "DOGE"},
  "calibration": null 또는 {"expression": "좀 빠진다", "actual_value": -3.2, "verified": true},
  "style_update": null 또는 {"tone": "반말", "depth": "간결"}
}
FORKER_META-->

2. 의도별 행동:
- alert (2-1): "BTC 10만 알려줘" → 단순 트리거 등록 (price_above). Intelligence 안 거침. 간결하게 확인.
- signal_trigger (2-2): 세 가지 경로:
  · 단순 조건 → 경량 알림 🔔: "BTC 10만 알려줘" → Base 실시간 매칭. 즉시.
  · 복잡 but 구조화 가능 → 시그널 트리거 🎯: "업비트 거래량 상위 3개가 BTC보다 높으면 알려줘"
    → 채팅 LLM이 조건을 코드 로직으로 분해 → 필요 데이터 Base에 Hot 추가 → 코드 폴링(~5분) 매칭.
    → FORKER 응답: "알겠어! 업비트 거래량 데이터 실시간 감시 시작할게. 조건 되면 바로 알려줄게 🔔"
  · LLM 판단 필요 → llm_evaluated 🧠: "시장 분위기가 공포로 전환되면 알려줘"
    → Base에 추가할 수 없는 데이터이거나 수치로 정의 불가능한 조건.
    → Patrol(1시간 주기)에서 LLM이 평가.
    → FORKER 응답: "이건 데이터만으로 판단하기 어려워서 내가 직접 주기적으로 체크할게.
      실시간으로 가능한 요청은 실시간으로 설정해줄 수 있어! 조건을 더 구체적으로 바꾸면
      실시간 감시로 전환할 수도 있어. 예를 들어 'Fear&Greed 25 이하면' 같은 식으로!"
- market_question (2-4): "VANA 왜 올라?" → 자율 서치 필요 표시. 서치 후 답변.
- general: 토론, 복기, 잡담 → Intelligence 바탕 대화. 기억할 만하면 에피소드 저장.
- review: 매매 복기 요청 → 에피소드에서 관련 매매 찾아 복기 지원.
- patrol_deferred (2-3): 실시간 불가 요청 → "다음 순찰(최대 1시간)에서 확인해줄게!" 안내.

3. 차트 이미지가 유저에게 도움될 상황이면 chart_needed: true 추가

4. 유저가 차트 이미지를 보냈을 때:
- 이미지 분석 (패턴, 지지/저항, 지표)
- 유저 의견과 실제 차트 대조
- 맞으면 유저 분석력 신뢰도↑, 틀리면 캘리브레이션
- should_save_episode: true로 표시

5. 위험 감지:
- FOMO 패턴 (급등 중 추격매수 의사)
- 연속 손실 후 과매매
- 손절 기준 무시
→ 부드럽게 경고. "너 원칙에서 손절 -5%라고 했잖아"
"""
```

### 4-2. 채팅 처리 로직 (src/core/chat.py)

```python
async def process_message(user_id: int, message_text: str, image_base64: str = None):
    """
    1. Intelligence 컨텍스트 구축
       - 최근 에피소드 5개 (Pinecone 유사 검색으로 관련 에피소드도 포함)
       - 투자 원칙 전체
       - 유저 스타일 (style_parsed)
       - 캘리브레이션 데이터
       - 현재 Base 데이터 (Hot 스트림만)
       - 보유 포지션
       - 최근 대화 10개

    2. LLM 호출 (Sonnet 4.5)
       - system: CHAT_SYSTEM_PROMPT (위 컨텍스트 주입)
       - messages: 최근 대화 히스토리 + 현재 메시지
       - 이미지 있으면 Vision 모드

    3. 응답 파싱
       - 텍스트 응답 추출 (<!--FORKER_META 앞까지)
       - FORKER_META JSON 파싱

    4. 후처리 (메타데이터 기반)
       a. intent == "alert" → UserTrigger 생성 (type="alert")
       b. intent == "signal_trigger" → UserTrigger 생성 (type="signal") + 에피소드 저장
       c. intent == "market_question" → 자율 서치 실행 (search.py) → 결과로 2차 응답
       d. should_save_episode → 에피소드 생성 + Pinecone upsert
       e. trigger_action → UserTrigger 생성
       f. base_addition → BaseStream 추가 (temperature="hot")
       g. calibration → Episode에 expression_calibration 추가
       h. style_update → User.style_parsed 업데이트
       i. chart_needed → Playwright로 차트 캡처 후 텔레그램으로 이미지 전송

    5. DB 저장
       - ChatMessage (role="user", content=message_text)
       - ChatMessage (role="assistant", content=response_text)

    6. 텔레그램 전송
       - 텍스트 응답
       - 차트 이미지 (있으면)
       - 인라인 키보드 (확인/아니야 버튼 — 매매 근거 확인 등)
    """
```

### 4-3. 자율 서치 (src/data/search.py)

```python
async def autonomous_search(user_id: int, query: str, user_language: str):
    """
    시장 질문 시 최적 소스 검색. 한국어 질문→영어 소스 포함, 영어 질문→한국 소스 포함.
    
    검색 순서 (비용 최적화):
    ① Base 데이터 확인 (비용 0) → 있으면 바로 응답
    ② 외부 API (CryptoPanic, CMC) → 뉴스/시장 데이터
    ③ Tavily 웹 검색 (한국어 + 영어 쿼리 모두)
    ④ 필요시 Playwright 브라우징 (거래소 공지 등)
    
    결과:
    - 수집된 데이터를 LLM에게 전달하여 유저 맥락 포함 답변 생성
    - 기억할 만하면 Intelligence 에피소드 저장
    - 차트가 도움되면 Playwright로 캡처 첨부
    
    Tavily 검색 시:
    - 한국어 질문이어도 영어 쿼리 추가: "VANA why pumping crypto"
    - 영어 질문이어도 한국 소스: "VANA 급등 이유"
    - search_depth="advanced" 사용
    """
```

---

## 📡 PHASE 5: 거래소 연동 + Q1 매매 감지 (1.5시간)

### 5-1. 거래소 매니저 (src/exchange/manager.py)

```python
# ccxt 라이브러리로 바이낸스/업비트/빗썸 통합 관리
#
# get_exchange(user_id, exchange_name) → ccxt 인스턴스
#   - DB에서 암호화된 키 복호화 → ccxt 인스턴스 생성 → 메모리에서만 사용
#
# fetch_balance(user_id, exchange) → {asset: amount}
# fetch_open_positions(user_id, exchange) → [{symbol, side, size, entry_price, pnl, leverage}]
# fetch_recent_trades(user_id, exchange, since_days=30) → [trades]
# fetch_funding_rates(exchange, symbols) → {symbol: rate}
#
# 업비트/빗썸 특이사항:
# - 선물 없음 (현물만)
# - KRW 마켓 (원화 가격)
# - 김프(한국 프리미엄) 계산: upbit_price / (binance_price * usd_krw_rate) - 1
```

### 5-2. 매매 감지 (src/exchange/trade_detector.py)

```python
async def poll_trades(user_id: int):
    """
    연결된 모든 거래소에서 새 매매 감지. 30초 주기 폴링.
    
    감지 흐름 (Q1):
    1. 마지막 체크 이후 새 거래 가져오기
    2. 자동 필터:
       - 극소액 거래 스킵 (잔고의 1% 미만)
       - 입출금 스킵
       - 더스트(dust) 정리 스킵
    3. 새 포지션 진입 감지 → handle_new_trade()
    4. 기존 포지션 청산 감지 → handle_trade_close()
    """

async def handle_new_trade(user_id: int, trade: dict):
    """
    매매 진입 감지 시:
    1. Trade DB에 기록 (status="open")
    2. FORKER 근거 추론 (Opus):
       - Intelligence에서 유저 패턴 조회
       - 현재 시장 상황 수집 (Base + API)
       - "이 유저라면 왜 이 시점에 이 매매를?" 추론
    3. 유저에게 확인 요청:
       "🔄 {symbol} {side} 감지! {추론된 근거} 맞지?"
       [맞아 ✅] [아니야 ❌]
    4. 콜백 처리:
       - 맞아 → forker_reasoning 저장, user_confirmed=True, 에피소드 생성
       - 아니야 → "그럼 왜 들어갔어?" 질문 → 유저 답변 → 실제 근거 저장 + 에피소드
    """

async def handle_trade_close(user_id: int, trade: dict, pnl: float):
    """
    청산 감지 시:
    1. Trade DB 업데이트 (status="closed", pnl)
    2. 결과 알림:
       - 수익: "📈 {symbol} +{pnl}%! {코멘터리}"
       - 손실: "📉 {symbol} {pnl}%. 같이 복기해볼까?"
    3. 코멘터리 (보유 중에도):
       - 유저 평균 익절/손절 대비 현재 수익률 비교
       - "너 평균 익절 +12%인데 넘었어" 같은 알림
    4. 손실 시 복기 제안:
       "같이 복기해볼까?
        ① 진입 근거: {reasoning}
        ② 시장 변화: {market_change}
        ③ 결과: {pnl}%
        ④ 교훈: {lesson}"
    5. 에피소드 영구 저장 (결과 + 교훈 포함)
    6. 위험 감지:
       - 연속 3회 이상 손실 → "연속 손실이야. 쉬어가는 것도 전략이야."
       - FOMO 패턴 → "급등 중 추격매수는 네 패턴이 아니야"
    """

async def monitor_positions(user_id: int):
    """
    보유 중인 포지션 모니터링 (30초 주기):
    - 현재 수익률 체크
    - 유저 평균 익절/손절 대비 비교
    - 임계점 도달 시 코멘터리 전송
    - 손절 기준(-5%) 도달 시 경고
    """
```

---

## 🔍 PHASE 6: Intelligence Module (1.5시간)

### 6-1. 에피소드 생성 (src/intelligence/episode.py)

```python
async def create_episode(
    user_id: int,
    episode_type: str,       # "trade" | "chat" | "feedback" | "signal" | "patrol"
    user_action: str,        # 유저 발화/행동 원문
    trade_data: dict = None,
    reasoning: str = None,
    trade_result: dict = None,
    feedback: str = None
):
    """
    에피소드 생성 트리거:
    - Q1: 매매 감지 시
    - Q2: 채팅에서 LLM이 "기억할 만하다" 판단 (should_save_episode)
    - Q4: 시그널/브리핑 피드백 + 매매 결과
    
    시장 상황 수집 (참조 범위 = 전체):
    1. Base 데이터 전체 (Hot + Warm)
    2. 관련 종목 API 데이터 (가격, 펀딩비, OI)
    3. 관련 뉴스 (CryptoPanic + 코인니스)
    4. 없으면 Tavily 브라우징 → 있으면 Base에 추가
    5. LLM(Sonnet)이 관련 있는 것만 선별하여 텍스트 요약
    
    유저 표현 캘리브레이션:
    - 유저 발언 vs 실제 데이터 대조
    - "좀 빠진다" → 실제 BTC -3.2% → calibration 저장
    
    벡터 임베딩:
    - 시장상황 + 유저행동 + 근거 + 결과를 하나의 텍스트로
    - Pinecone upsert (namespace: user_{telegram_id})
    
    리턴: Episode 객체
    """
```

### 6-2. Intelligence 컨텍스트 구축

```python
async def build_intelligence_context(user_id: int, current_query: str = None) -> str:
    """
    LLM에게 전달할 Intelligence 컨텍스트 구축.
    채팅, 시그널 판단, 매매 근거 추론 등 모든 LLM 호출에 사용.
    
    구성:
    1. 유저 프로필: 스타일, 언어, 티어
    2. 투자 원칙 (Q3) 전체
    3. 학습된 패턴 요약: 주 매매 종목, 선호 전략, 평균 수익/손실, 매매 빈도
    4. 표현 캘리브레이션: {"좀 빠진다": -3%, "많이": -8%}
    5. 최근 에피소드 5개 (시간순)
    6. 유사 에피소드 3개 (current_query로 Pinecone 검색, 있으면)
    7. 현재 보유 포지션
    8. 최근 시그널 + 피드백
    
    프롬프트 캐싱:
    - 1~4번은 잘 안 바뀜 → cache_control={"type": "ephemeral"} 설정
    - 5~8번은 동적
    
    리턴: 구조화된 텍스트 (시스템 프롬프트에 삽입용)
    """
```

### 6-3. 패턴 분석 (src/intelligence/pattern.py)

```python
async def analyze_patterns(user_id: int) -> dict:
    """
    유저의 매매 패턴을 에피소드 기반으로 분석.
    온보딩 초기 리포트 + 싱크로율 계산에 사용.
    
    분석 항목:
    - 주 매매 종목 Top 5
    - 선물/현물 비율
    - 평균 보유 시간
    - 승률, 평균 수익/손실, 최대 수익/손실
    - 진입 패턴: 어떤 조건에서 매매하는 경향 (펀딩비, 거래대금, 뉴스 등)
    - 시간대별 매매 빈도
    - 손절 패턴: 평균 손절 수준, 늦는 경향 있는지
    - 익절 패턴: 평균 익절 수준, 너무 빠른지/늦는지
    """
```

---

## 📊 PHASE 7: Tier 1 감시 시스템 (1.5시간)

### 7-1. Base 데이터 스트림 (src/monitoring/base.py)

```python
class BaseManager:
    """
    Tier 1 Base — 실시간 데이터 스트림 + 온도 관리
    AI 미사용. 비용 0.
    
    기본 프리셋 (Default Base) — 온보딩 완료 시 자동 생성:
    - 시세: BTC, ETH + CMC Top 20
    - 파생: 펀딩비, OI, 청산 (바이낸스 주요 종목)
    - 뉴스: CryptoPanic + 코인니스
    - 지표: Fear&Greed, 김프
    - 거래소 스프레드: 업비트 vs 빗썸 vs 바이낸스
    - 유저 거래소: 연결된 거래소 포지션/잔고
    
    온도 관리 (Redis 캐시):
    🔴 Hot (최근 7일 언급): 실시간 폴링 (10초)
    🟡 Warm (7~30일 미언급): 느린 폴링 (30분)
    🔵 Cold (30일+ 미언급): Patrol에서만 체크
    
    온도 전환:
    - 유저가 종목 언급 → 해당 스트림 즉시 Hot
    - 7일 미언급 → Hot→Warm (자동)
    - 30일 미언급 → Warm→Cold (자동)
    - 절대 삭제하지 않음 (재언급 시 Cold→Hot 즉시 복원)
    
    확장 트리거:
    1. 유저가 없는 데이터 질문 → 조회 후 Base에 추가
    2. 에피소드 생성 시 데이터 없을 때 → 브라우징 후 추가
    3. LLM 자동 추가 → 패턴 기반 ("상장 뉴스 반응" → 관련 스트림)
    
    데이터 저장:
    - Hot: Redis에 캐싱 (TTL 60초)
    - DB: BaseStream 테이블에 last_value 업데이트
    """
    
    async def get_hot_data(self, user_id: int) -> dict:
        """Hot 스트림의 현재 데이터를 Redis에서 가져옴"""
    
    async def update_temperature(self, user_id: int, symbol: str):
        """종목 언급 시 온도 업데이트 (→ Hot)"""
    
    async def check_triggers(self, user_id: int, data: dict):
        """새 데이터가 들어올 때마다 UserTrigger 조건 매칭"""
```

### 7-2. User Trigger (src/monitoring/trigger.py)

```python
class TriggerManager:
    """
    경량 알림 🔔: 단순 조건 → 즉시 전달. LLM 없음. 비용 0.
    시그널 트리거 🎯: 복합 조건 → Tier 2→3 파이프라인.
    
    조건 타입들:
    - price_above / price_below: 가격 도달
    - funding_below / funding_above: 펀딩비
    - volume_spike: 거래대금 급증 (배수)
    - oi_change: OI 변화율
    - kimchi_premium: 김프 임계치
    - news_keyword: 뉴스 키워드 매칭
    - composite: 복합 조건 — 코드로 구조화 가능한 동적 조건 포함 (AND/OR)
    - llm_evaluated: Base에 추가 불가능한 데이터이거나 수치로 정의 불가능한 조건. Patrol에서만 평가.
    
    === 3단계 트리거 체계 ===
    
    ① 경량 알림 🔔 (즉시):
       단순 임계값 → Base 데이터 실시간 매칭. LLM 없음. 비용 0.
       예: "BTC 10만 되면" → {type: "price_above", symbol: "BTC", value: 100000}
    
    ② 시그널 트리거 🎯 (준실시간 ~5분):
       복잡하지만 코드로 구조화 가능한 조건.
       채팅 LLM이 조건을 코드 로직으로 분해 → 필요 데이터 Base에 Hot 추가 → 코드 폴링 매칭.
       예: "업비트 거래량 상위 3개 > BTC"
       → LLM 분해: {type: "composite", conditions: [{
             data_source: "upbit_volume_ranking",
             logic: "top3_volume > btc_volume"
           }]}
       → Base에 "upbit_volume_ranking" 스트림 Hot 추가 (~5분 폴링)
       → 매 갱신마다 코드로 조건 매칭 → 충족 시 Tier 2→3
       → 유저 응답: "실시간 감시 시작할게. 조건 되면 바로 알려줄게!"
    
    ③ llm_evaluated 🧠 (Patrol 주기, ~1시간):
       수치로 정의 불가능하거나, 브라우징 필요한 외부 데이터가 필요한 조건.
       예: "시장 분위기가 공포로 전환되면", "트위터에서 SOL 관련 FUD 나오면"
       → {type: "llm_evaluated",
          description: "시장 분위기 공포 전환 여부",
          eval_prompt: "...",
          data_needed: ["sentiment", "news", "social"]}
       → Patrol이 매 순찰마다 데이터 수집 + LLM 판단
       → 유저 응답: "이건 내가 직접 주기적으로 체크할게. 조건을 숫자로 구체화하면
         실시간 감시로 전환할 수도 있어! 예: 'Fear&Greed 25 이하면'"
    
    핵심 UX 원칙:
    - LLM이 요청을 받으면 최대한 ①②로 분류하여 실시간 처리
    - ③이 되는 경우, "실시간 가능한 요청은 실시간 설정 가능" + 구체화 제안
    - 유저에게 절대 "안 돼"가 아닌 "이렇게 하면 실시간도 돼" 방향으로 안내
    
    생성 주체:
    - 유저 직접 요청 (채팅 LLM이 분류 + ①②③ 자동 결정)
    - LLM이 Intelligence 기반 자동 생성 (72시간 무반응 시 자동 삭제)
    - Patrol이 생성/갱신/삭제
    """
    
    async def evaluate_all(self, user_id: int, current_data: dict):
        """모든 활성 트리거 조건 체크. Base 데이터 업데이트마다 호출."""
    
    async def fire_alert(self, user_id: int, trigger: UserTrigger):
        """경량 알림 즉시 전송"""
    
    async def fire_signal(self, user_id: int, trigger: UserTrigger):
        """시그널 트리거 → Tier 2 → Tier 3 파이프라인 시작"""
```

### 7-3. Patrol 자율 순찰 (src/monitoring/patrol.py)

```python
class PatrolService:
    """
    Pro 요금제: 1시간 주기 순찰. APScheduler로 스케줄링.
    한국어 + 영어 소스 모두 순찰.
    
    순찰 범위:
    1. Base 전체 종합 체크:
       - 모든 Hot/Warm 스트림의 변화 분석
       - 이상 징후 감지 (급등/급락, OI 급변, 펀딩비 극단값)
    
    2. llm_evaluated 트리거 평가 (③만 여기서 처리):
       - ①경량 알림, ②시그널 트리거는 Base에서 실시간/준실시간 처리됨
       - Patrol은 수치로 정의 불가능한 ③llm_evaluated 트리거만 담당
       - 각 트리거의 data_needed 수집 (브라우징, 뉴스, 소셜 등)
       - LLM에게 eval_prompt + 데이터 전달 → 조건 충족 여부 판단
       - 충족 시 → 경량 알림 또는 Tier 2→3 파이프라인
       - 예: "시장 분위기가 공포로 전환" → 뉴스/센티먼트 수집 → LLM 판단
    
    3. 브라우징 필요 데이터:
       - CoinGlass (롱숏비, 청산 히트맵)
       - 거래소 공지 (상장, 이벤트, 입출금)
       - 블루밍비트/블록미디어 (한국 뉴스)
       - CoinTelegraph/The Block (글로벌)
    
    4. 유저 패턴 기반 능동 서치:
       - Intelligence에서 유저 관심 영역 파악
       - 관련 종목/이슈 적극적 모니터링
       - "이 유저는 펀딩비에 반응" → 펀딩비 변화 특별 감시
    
    5. 실시간 불가 요청 (2-3) 처리:
       - 채팅에서 patrol_deferred로 분류된 요청
       - 이번 순찰에서 처리하고 결과 전송
    
    추가 역할:
    - Base 온도 관리: 7일/30일 기준 Hot→Warm→Cold 전환
    - User Trigger 자동 생성/갱신/삭제
    - 비활성 유저(24시간+ 미접속) 감지 → Patrol 주기 자동 확대
    
    LLM 사용: Sonnet 4.5 (순찰 분석용)
    
    순찰 결과:
    - 유저에게 알릴 이슈 발견 시 → 시그널 트리거 발동 또는 자율 브리핑
    - PatrolLog DB에 기록
    """
    
    async def run_patrol(self, user_id: int):
        """1시간 주기 순찰 실행"""
    
    async def check_base_anomalies(self, user_id: int) -> list:
        """Base 데이터 이상 징후 감지"""
    
    async def browse_sources(self, user_id: int) -> list:
        """Tavily로 브라우징 소스 체크"""
    
    async def process_deferred_requests(self, user_id: int) -> list:
        """대기 중인 요청 처리"""
```

---

## ⚡ PHASE 8: Tier 2 수집 + Tier 3 판단 (1시간)

### 8-1. Tier 2 심층 수집 (src/monitoring/collector.py)

```python
async def collect_deep(user_id: int, trigger: UserTrigger) -> dict:
    """
    시그널 트리거 발동 시, Intelligence 기반 심층 수집.
    
    수집 순서 (비용 최적화 — ①②로 충분하면 ③④ 안 씀):
    ① Base 데이터 (비용 0)
    ② 외부 API: CMC, CoinGlass, CryptoPanic (비용 저)
    ③ Tavily 웹 검색 — 한국어 + 영어 동시 (비용 중)
    ④ Playwright 브라우징 — 거래소 공지, 트위터 등 (비용 높)
    
    수집 범위:
    - 트리거 관련 종목의 모든 데이터
    - BTC/ETH 전체 시장 상황
    - 관련 뉴스/이벤트 (최근 24시간)
    - 기술 지표 (RSI, MACD, 볼린저밴드)
    - 펀딩비, OI, 청산 데이터
    - 거래대금 변화
    
    리턴: 수집된 데이터 구조화 dict
    """
```

### 8-2. Tier 3 AI 판단 (src/monitoring/judge.py)

```python
async def judge_signal(user_id: int, collected_data: dict, trigger: UserTrigger) -> Signal:
    """
    Tier 2 수집 + Intelligence → "너처럼 봤을 때" 판단.
    Pro 요금제: Opus 4.5 사용.
    
    판단 입력:
    📊 Tier 2 수집 데이터
    🧠 Intelligence Module: 에피소드, 투자 원칙, 매매 패턴, 캘리브레이션, 스타일
    📍 유저 현재 상태: 보유 포지션, 최근 결과, 대화 맥락
    
    판단 출력:
    1. 매매 시그널 (signal_type="trade_signal"):
       - direction: "long" | "short" | "exit" | "watch"
       - reasoning: "너처럼 봤을 때" 판단 이유
       - counter_argument: 반대 근거 (항상 포함!)
       - confidence: 0~1 확신도
       - stop_loss: 손절 기준 (유저 원칙 반영)
       - chart_needed: 차트 캡처 첨부 여부
    
    2. 자율 브리핑 (signal_type="briefing"):
       - 유저 패턴상 관심 가질 상황
       - 근거 + 관련 차트
    
    시그널 전송 포맷:
    "🎯 {symbol} {direction} 상황
    
    📊 판단 근거:
    {reasoning}
    
    ⚠️ 반대 근거:
    {counter_argument}
    
    📍 확신도: {confidence}%
    🛑 손절: {stop_loss}
    
    [📸 차트 이미지 첨부]
    
    어떻게 생각해?"
    [동의 ✅] [아닌 거 같아 ❌]
    
    톤: "FORKER 추천"이 아닌 "너처럼 봤을 때"
    유저 설정 언어로 전달.
    
    시그널 상한 체크: Pro 기준 일 5회. 초과 시 "오늘 시그널 5회 다 썼어. 내일 리셋!"
    """
```

### 8-3. 차트 캡처 (src/data/chart.py)

```python
async def capture_chart(symbol: str, timeframe: str = "1h") -> bytes:
    """
    Playwright로 TradingView 차트 스크린샷 캡처.
    
    1. TradingView 위젯 HTML 생성 (로컬)
       - 심볼: BINANCE:{symbol}USDT
       - 타임프레임: 1h / 4h / 1D (LLM이 선택)
       - 지표: RSI, 볼린저밴드
       - 다크 테마
    
    2. Playwright headless Chromium으로 렌더링
    3. 스크린샷 캡처 (PNG)
    4. 텔레그램으로 전송
    
    캡처 대상:
    - 해당 종목 캔들 차트
    - 주요 기술지표 (RSI, 볼밴)
    - LLM이 상황에 맞는 타임프레임 선택
    
    비용: Playwright 기존 인프라 활용. 추가 비용 ≈ 0.
    """
```

---

## 🔄 PHASE 9: Feedback 순환 학습 (30분)

### 9-1. 피드백 처리 (src/feedback/processor.py)

```python
async def process_signal_feedback(signal_id: int, user_feedback: str, agreed: bool):
    """
    시그널/브리핑 피드백 처리.
    
    피드백 유형:
    💬 자연어 피드백: "좋은 포인트인데 거래대금이 좀 부족해"
       → 동의→강화, 반대→교정, 세부조정→반영
    
    처리:
    1. Signal DB 업데이트 (user_feedback, user_agreed)
    2. 에피소드 생성 (type="feedback")
    3. Intelligence 패턴 업데이트:
       - 동의: 유사 상황 confidence ↑
       - 반대: 유저가 다르게 보는 관점 학습
       - 세부조정: 조건 세부 튜닝
    """

async def process_trade_result_feedback(trade_id: int):
    """
    매매 결과 자동 피드백 (Q4).
    시그널 후 매매 → 결과 자동 감지 → Q1 + Intelligence 동시 교정.
    
    처리:
    1. 해당 시그널 찾기
    2. Signal DB 업데이트 (trade_followed=True, trade_result_pnl)
    3. Q1 에피소드와 연결
    4. Intelligence 업데이트:
       - 시그널 → 매매 → 수익: 패턴 강화
       - 시그널 → 매매 → 손실: 패턴 교정
       - 시그널 → 미매매: 유저가 다른 판단 (학습)
    """
```

---

## 🚀 PHASE 10: 통합 + 스케줄러 + 배포 (1시간)

### 10-1. FastAPI 앱 (src/main.py)

```python
"""
FastAPI app with lifespan events:

startup:
1. DB 테이블 생성 (create_all)
2. Redis 연결
3. Pinecone 인덱스 연결
4. Playwright 브라우저 시작
5. 텔레그램 봇 시작 (Application.run_polling)
6. APScheduler 시작:
   - 매매 감지 폴링: 30초마다 (모든 활성 유저)
   - Patrol 순찰: 유저별 1시간마다
   - Base 온도 관리: 1시간마다
   - 일일 시그널 카운트 리셋: 매일 00:00 UTC
   - 트리거 자동 삭제: 72시간 미반응 LLM 생성 트리거
7. Base 데이터 폴링 시작 (Hot: 10초, Warm: 30분)

shutdown:
1. 스케줄러 종료
2. 텔레그램 봇 종료
3. Playwright 브라우저 종료
4. DB/Redis 연결 해제

health check:
GET /health → {"status": "ok", "users": active_count}

API-First 엔드포인트 (추후 Electron 앱용, 지금은 내부 사용):
POST /auth/register
POST /auth/exchange
POST /chat/message
GET /user/sync
GET|PUT /user/principles
GET /intelligence/episodes
GET /monitoring/base
GET /monitoring/triggers
"""
```

### 10-2. Railway 배포

```
1. GitHub 리포 생성 + 코드 푸시
2. Railway에서 새 프로젝트 생성
3. GitHub 리포 연결
4. 애드온 추가: PostgreSQL + Redis
5. 환경변수 설정 (.env의 모든 키)
6. 자동 배포 (railway up)
7. 텔레그램 봇 웹훅 설정 (polling → webhook 전환 가능)
```

---

## 📌 구현 시 반드시 지킬 규칙

### LLM 비용 최적화
1. **채팅 의도 분류는 별도 LLM 호출하지 않음** — 채팅 응답에서 동시 처리 (FORKER_META)
2. **프롬프트 캐싱 반드시 적용** — system prompt + Intelligence에 cache_control
3. **Base/Trigger 매칭은 AI 미사용** — 단순 조건 비교 (Python 코드)
4. **Tier 2 수집은 ①②로 충분하면 ③④ 안 씀** — 비용 절약

### 보안
1. 거래소 API 키는 AES-256 암호화 저장, 로그 절대 금지
2. 읽기전용 API만 수집, 출금/주문 권한 불필요 — 온보딩에서 강조
3. "TRADEFORK는 매매를 대신 실행하지 않음" 명시
4. Rate Limit: Pro 120/min

### 다국어
1. 유저 언어 자동 감지 (첫 메시지 기반) + 수동 설정 가능
2. 한국어 유저도 영어 뉴스 수집, 영어 유저도 한국 시장 뉴스 수집
3. LLM이 유저 언어로 번역/요약
4. 표현 캘리브레이션도 다국어 ("좀 빠진다", "a bit down" 등)

### UX
1. 톤: "FORKER 추천" 아니라 "너처럼 봤을 때"
2. 유저 말투에 맞춤 (반말이면 반말)
3. 시그널에 항상 반대 근거 포함
4. 위험 감지 시 부드럽게 경고 (유저 원칙 인용)
5. 인라인 키보드로 빠른 피드백 ([맞아] [아니야], [동의] [아닌 거 같아])

---

## 🛠️ 개발 순서 요약

| 순서 | Phase | 예상 시간 | 핵심 |
|------|-------|-----------|------|
| 1 | DB 스키마 + 프로젝트 초기화 | 1h | 모든 테이블 + requirements + Railway 설정 |
| 2 | 보안 + LLM + Pinecone | 30min | 암호화, Anthropic 클라이언트, 벡터 스토어 |
| 3 | 텔레그램 봇 + 온보딩 | 2h | /start 플로우 + 거래소 등록 + 초기 리포트 |
| 4 | Q2 채팅 엔진 | 2h | 가장 중요! 의도 분류 + 자율 서치 + 에피소드 |
| 5 | 거래소 + Q1 매매 감지 | 1.5h | ccxt + 매매 감지 + 근거 추론 + 복기 |
| 6 | Intelligence Module | 1.5h | 에피소드 + 캘리브레이션 + 패턴 분석 |
| 7 | Tier 1 감시 | 1.5h | Base 온도 + Trigger + Patrol |
| 8 | Tier 2 + 3 | 1h | 심층 수집 + Opus 판단 |
| 9 | Feedback | 30min | 피드백 → Intelligence 순환 |
| 10 | 통합 + 배포 | 1h | 스케줄러 + Railway 배포 |

---

## ⚠️ 빌드 시작 전 유저가 해야 할 것

### 1. Railway 프로젝트 셋업
```bash
# Railway CLI 설치
npm install -g @railway/cli

# 로그인 + 프로젝트 생성
railway login
railway init

# 애드온 추가 (대시보드에서)
# - PostgreSQL
# - Redis
```

### 2. Pinecone 인덱스 생성
```
Pinecone 대시보드에서:
- Index Name: tradefork-episodes
- Dimensions: 1024
- Metric: cosine
- Cloud: AWS
- Region: us-east-1 (또는 가장 가까운)
- Type: Serverless
```

### 3. 텔레그램 봇 생성
```
@BotFather에서:
1. /newbot → 이름: TRADEFORK
2. Bot Token 복사
3. /setcommands:
   start - 시작 + 온보딩
   sync - 싱크로율 조회
   principles - 투자 원칙 조회/수정
   help - 도움말
```

### 4. 환경변수 설정
```bash
# Railway 대시보드 Variables 탭에서:
TELEGRAM_BOT_TOKEN=xxx
ANTHROPIC_API_KEY=xxx
PINECONE_API_KEY=xxx
PINECONE_INDEX_NAME=tradefork-episodes
ENCRYPTION_KEY=xxx           # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TAVILY_API_KEY=xxx
CMC_API_KEY=xxx
CRYPTOPANIC_API_KEY=xxx      # 없어도 됨

# Railway가 자동 제공:
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
```

### 5. Playwright 브라우저 설치 (Railway에서 자동)
```
railway.toml의 startCommand에 포함:
playwright install chromium --with-deps
```

---

## 🔁 Claude Code에게 주는 최종 지시

**이 프롬프트의 모든 Phase를 순서대로 구현하라. 각 Phase 완료 후 다음으로 넘어가라.**

구현 원칙:
1. **모든 함수에 async/await 사용** (FastAPI + python-telegram-bot 모두 async)
2. **에러 핸들링 철저** — 거래소 API 실패, LLM 타임아웃 등 모든 케이스
3. **로깅 필수** — 하지만 거래소 API 키는 절대 로그에 남기지 말 것
4. **타입 힌트 사용** — 모든 함수에 파라미터/리턴 타입
5. **각 파일은 단일 책임** — 하나의 파일이 너무 커지면 분리
6. **테스트 가능하게** — 외부 의존성은 인터페이스로 추상화

시작하라. Phase 1부터.