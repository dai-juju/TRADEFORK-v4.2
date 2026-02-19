# TRADEFORK — 투자 분신 FORKER 텔레그램 봇

## 프로젝트 개요

TRADEFORK는 암호화폐 투자 지능 플랫폼이다. 각 유저에게 **"FORKER"**라는 개인 AI 에이전트가 배정되어, 유저의 매매 패턴을 학습하고 **"너처럼 봤을 때"** 시그널을 제공하는 투자 분신이다.

**현재 빌드 범위: Pro 요금제($69/월)** — Basic/Enterprise는 추후 확장.

### 코어 파이프라인 (모든 기능의 기반)
```
Q(수집) → Intelligence(학습) → Tier 1/2/3(감시/판단) → 시그널 → Feedback → Q (무한 순환)
```

### 상세 스펙 문서 위치
- **기능명세서 원본**: `docs/spec-v42.html` (23 Sections, 전체 파이프라인/온보딩/요금제/아키텍처/보안)
- **Phase별 개발 프롬프트**: `docs/dev-prompt.md` (Phase 1~10, DB스키마/코드구조/로직 상세)
- 구현 시 반드시 해당 Phase를 `docs/dev-prompt.md`에서 읽고 따를 것

---

## 기술 스택

| 레이어 | 기술 | 비고 |
|--------|------|------|
| Runtime | Python 3.11+ | async 전체 |
| Framework | FastAPI + uvicorn | API-First 설계 |
| Telegram | python-telegram-bot v21+ | async, Application 클래스 |
| DB | PostgreSQL (Railway 애드온) | SQLAlchemy 2.0 async + asyncpg |
| Cache | Redis (Railway 애드온) | Base 데이터 캐싱 + Trigger 매칭 |
| Vector DB | Pinecone Serverless | index: tradefork-episodes, dim: 1024, cosine |
| LLM | Anthropic API | 모델 라우팅 아래 참조 |
| 거래소 | ccxt | 바이낸스/업비트/빗썸 통합 |
| 웹서치 | Tavily API | 자율 서치 + Patrol |
| 차트캡처 | Playwright (headless Chromium) | TradingView 스크린샷 |
| 뉴스 | CryptoPanic API + 코인니스 크롤링 | 글로벌 + 한국 |
| 마켓데이터 | CoinMarketCap API | 시총, 순위, Fear&Greed |
| 배포 | Railway (PaaS) | PostgreSQL/Redis 애드온 |

---

## LLM 모델 라우팅 (Pro 요금제)

| 기능 | 모델 | model string |
|------|------|-------------|
| 채팅 (Q2) | Sonnet 4.5 | `claude-sonnet-4-5-20250929` |
| 에피소드 생성 | Sonnet 4.5 | `claude-sonnet-4-5-20250929` |
| Patrol 분석 | Sonnet 4.5 | `claude-sonnet-4-5-20250929` |
| **시그널 판단 (Tier 3)** | **Opus 4.5** | `claude-opus-4-6` |
| **매매 근거 추론 (Q1)** | **Opus 4.5** | `claude-opus-4-6` |
| **온보딩 초기 분석** | **Opus 4.5** | `claude-opus-4-6` |

### 프롬프트 캐싱 필수
- system prompt에 `cache_control={"type": "ephemeral"}` 반드시 추가
- Intelligence 컨텍스트 중 정적 부분(유저 프로필, 원칙, 스타일)도 캐싱
- 이것만으로 input 비용 최대 90% 절감

---

## Pro 요금제 제한

| 항목 | 값 |
|------|----|
| Patrol 주기 | 1시간 (일 24회) |
| 시그널 상한 | 일 5회 |
| 거래소 연결 | 최대 3개 |
| Rate Limit | 120 req/min |

---

## 프로젝트 구조

```
tradefork-bot/
├── CLAUDE.md
├── docs/
│   ├── spec-v42.html              # 기능명세서 v4.2
│   └── dev-prompt.md              # Phase별 개발 프롬프트
├── src/
│   ├── __init__.py
│   ├── main.py                    # FastAPI + lifespan (startup/shutdown)
│   ├── config.py                  # 환경변수 + 설정 상수
│   ├── bot/                       # 텔레그램 봇
│   │   ├── handlers.py            # /start, /sync, /principles, /dailybrief, /help + 메시지
│   │   ├── keyboards.py           # 인라인 키보드 (온보딩, 피드백, 브리핑시간)
│   │   └── formatter.py           # 메시지 포매팅
│   ├── core/                      # 코어 비즈니스 로직
│   │   ├── auth.py                # 유저 등록, 거래소 연결
│   │   ├── chat.py                # Q2 채팅 처리 (의도 분류 + 응답 동시)
│   │   ├── onboarding.py          # 온보딩 (30일 분석 → 초기 리포트)
│   │   ├── briefing.py            # 데일리 브리핑 (시장+포지션+뉴스+트리거+차트+코멘터리)
│   │   └── sync_rate.py           # 싱크로율 계산
│   ├── intelligence/              # FORKER의 뇌
│   │   ├── episode.py             # 에피소드 CRUD + 시장상황 수집
│   │   ├── calibration.py         # 표현 캘리브레이션 + 스타일 학습
│   │   ├── pattern.py             # 매매 패턴 분석
│   │   └── vector_store.py        # Pinecone 임베딩/유사검색
│   ├── monitoring/                # Tier 1/2/3 감시
│   │   ├── base.py                # Base 스트림 + 온도 관리 (Hot/Warm/Cold)
│   │   ├── trigger.py             # 3단계 트리거 (①경량 ②구조화 ③LLM평가)
│   │   ├── patrol.py              # 자율 순찰 (1시간 주기)
│   │   ├── collector.py           # Tier 2 심층 수집
│   │   └── judge.py               # Tier 3 AI 판단 (Opus)
│   ├── exchange/                  # 거래소 연동
│   │   ├── manager.py             # ccxt 통합 매니저
│   │   ├── trade_detector.py      # Q1 매매 감지 + 필터
│   │   └── position_tracker.py    # 포지션/잔고 추적
│   ├── data/                      # 외부 데이터
│   │   ├── market.py              # CMC, CoinGlass, CoinGecko
│   │   ├── news.py                # CryptoPanic + 코인니스
│   │   ├── search.py              # Tavily 웹서치
│   │   └── chart.py               # Playwright 차트 캡처
│   ├── llm/                       # LLM 통합
│   │   ├── client.py              # Anthropic 클라이언트 (캐싱 + 라우팅)
│   │   ├── prompts.py             # 모든 시스템 프롬프트
│   │   └── vision.py              # 이미지 분석
│   ├── feedback/
│   │   └── processor.py           # 피드백 → Intelligence 순환
│   ├── security/
│   │   └── encryption.py          # AES-256 거래소 키 암호화
│   └── db/
│       ├── models.py              # SQLAlchemy 모델 전체
│       ├── session.py             # async 세션 팩토리
│       └── migrations.py          # 테이블 자동 생성
├── requirements.txt
├── Procfile
├── railway.toml
├── .env                           # 로컬 개발용 (git에 올리지 말 것)
└── .gitignore
```

---

## 핵심 아키텍처 규칙

### Q2 채팅 — 의도 분류 동시 처리
채팅 LLM이 응답 생성 + 의도 분류를 **한 번의 호출로 동시 처리**. 별도 LLM 호출 금지.
응답 끝에 `<!--FORKER_META {...} FORKER_META-->` JSON 주석으로 메타데이터 포함.
의도 분류: `alert` | `signal_trigger` | `market_question` | `general` | `review` | `patrol_deferred`

### 3단계 트리거 체계 (Section 12)
유저 요청을 **최대한 실시간으로 처리**하는 것이 원칙:

| 단계 | 이름 | 처리 방식 | 지연 | 예시 |
|------|------|-----------|------|------|
| ① | 경량 알림 🔔 | Base 실시간 매칭. LLM 없음 | 즉시 | "BTC 10만 되면 알려줘" |
| ② | 시그널 트리거 🎯 | LLM이 조건→코드 분해. Base에 데이터 Hot 추가 | ~5분 | "업비트 거래량 상위3 > BTC면" |
| ③ | LLM 평가 🧠 | 수치 정의 불가. Patrol에서 LLM이 주기적 평가 | ~1시간 | "시장 분위기 공포 전환되면" |

- LLM이 요청 받으면 **최대한 ①②로 끌어올려** 실시간 처리
- ③일 때: "실시간 가능한 건 실시간으로 설정 가능" 안내 + 조건 구체화 제안
- 예: "Fear&Greed 25 이하면" 같이 바꾸면 ③→① 전환 가능

### Base 온도 관리 (Section 11)
데이터는 절대 삭제하지 않음. 3단계 온도로 비용 최적화:
- 🔴 Hot (7일 내 언급): 실시간 폴링 (~10초). Redis 캐싱.
- 🟡 Warm (7~30일): 느린 폴링 (30분)
- 🔵 Cold (30일+): 스트림 중단. Patrol에서만 체크.
- 재언급 시 Cold→Hot 즉시 복원

### /principles — 4가지 조작
LLM이 유저 의도를 자동 분류하여 처리:
- **추가**: "레버리지 최대 10배 추가해줘" → 기존 유지 + 신규 추가
- **수정**: "1번 -5%를 -7%로 바꿔" → 해당 원칙만 수정
- **삭제**: "3번 삭제해" → 비활성화
- **전체교체**: "전부 바꿀게. ~" → 기존 전체 비활성화 + 새로 생성
- /principles 후 60초 타임아웃 → 자동 해제 → 일반 채팅 복귀

### Intelligence Module (Section 7~8)
모든 LLM 호출에 Intelligence 컨텍스트 주입:
1. 유저 프로필 (스타일, 언어)
2. 투자 원칙 (Q3) 전체
3. 학습된 패턴 (주 종목, 선호 전략, 평균 수익/손실)
4. 표현 캘리브레이션 ("좀 빠진다"=-3%)
5. 최근 에피소드 5개 + Pinecone 유사 에피소드 3개
6. 현재 보유 포지션
7. 최근 시그널 + 피드백

에피소드 생성 트리거: Q1(매매감지), Q2(LLM 판단 "기억할 만하다"), Q4(피드백+결과)

### Q1 매매 감지 흐름 (Section 3)
```
거래소 API 매매 감지 → 필터(극소액/입출금 스킵) → FORKER 근거 추론(Opus)
→ 유저 확인 요청 [맞아/아니야] → 에피소드 저장
→ 보유 중: 코멘터리 ("평균 익절 +12%인데 넘었어")
→ 청산: 결과 기록 + 손실 시 복기 제안
→ 위험 감지: FOMO/연속손실 과매매 경고
```

---

## 절대 규칙 (위반 시 버그/보안 문제)

### 코드 품질
1. **모든 함수 async/await** — FastAPI, telegram bot, DB, 외부 API 전부
2. **타입 힌트 필수** — 모든 함수의 파라미터 + 리턴 타입
3. **에러 핸들링** — 거래소 API 실패, LLM 타임아웃, DB 연결 끊김 전부 처리
4. **import 누락 금지** — 매 파일 작성 후 import 확인

### 보안
5. **거래소 API 키 AES-256 암호화** — DB에 암호화 blob만 저장
6. **복호화는 런타임 메모리에서만** — 사용 후 즉시 폐기
7. **로그에 API 키/시크릿 절대 노출 금지**
8. **읽기전용 API만 수집** — 출금/주문 권한 불필요. 온보딩에서 강조.
9. **"TRADEFORK는 매매를 대신 실행하지 않음"** 항상 명시

### LLM 비용 최적화
10. **채팅 의도 분류 = 별도 LLM 호출 금지** — FORKER_META로 동시 처리
11. **프롬프트 캐싱 반드시 적용** — system prompt + Intelligence 정적 부분
12. **Base/Trigger 매칭 = AI 미사용** — Python 코드로 단순 조건 비교
13. **Tier 2 수집 = ①②로 충분하면 ③④ 안 씀**

### UX / 톤
14. **"FORKER 추천" 아닌 "너처럼 봤을 때"** — 투자 분신 컨셉
15. **유저 말투에 맞춤** — 반말이면 반말, 간결하면 간결
16. **시그널에 항상 반대 근거 포함**
17. **위험 감지 시 부드럽게 경고** — 유저 원칙 인용
18. **다국어** — 한국어 유저도 영어 뉴스 수집, 영어 유저도 한국 뉴스 수집

---

## 환경변수 (.env)

```
TELEGRAM_BOT_TOKEN=
ANTHROPIC_API_KEY=
PINECONE_API_KEY=
PINECONE_INDEX_NAME=tradefork-episodes
ENCRYPTION_KEY=              # Fernet.generate_key()
TAVILY_API_KEY=
CMC_API_KEY=
CRYPTOPANIC_API_KEY=
BINANCE_API_KEY=             # 테스트용
BINANCE_API_SECRET=          # 테스트용
DATABASE_URL=                # Railway PostgreSQL
REDIS_URL=                   # Railway Redis
```

---

## 개발 순서 (Phase 1→10)

각 Phase의 상세 구현 내용은 `docs/dev-prompt.md` 참조.

| Phase | 내용 | 예상 시간 |
|-------|------|-----------|
| 1 | DB 스키마 + 프로젝트 초기화 + requirements + Railway 설정 | 1h |
| 2 | 보안(AES-256) + LLM 클라이언트(캐싱+라우팅) + Pinecone | 30min |
| 3 | 텔레그램 봇 + 온보딩 전체 플로우 (/start→초기 리포트) | 2h |
| 4 | Q2 채팅 엔진 (의도 분류 + 자율 서치 + 에피소드) — 핵심 | 2h |
| 5 | 거래소 연동 + Q1 매매 감지/근거추론/복기 | 1.5h |
| 6 | Intelligence Module (에피소드 + 캘리브레이션 + 패턴) | 1.5h |
| 7 | Tier 1 감시 (Base 온도 + 3단계 Trigger + Patrol) | 1.5h |
| 8 | Tier 2 심층 수집 + Tier 3 AI 판단 (Opus) | 1h |
| 9 | Feedback 순환 학습 | 30min |
| 10 | 통합 + 스케줄러(APScheduler) + Railway 배포 | 1h |

**지시 방법**: `"docs/dev-prompt.md의 PHASE N을 읽고 구현해"` — 한 번에 하나의 Phase만.