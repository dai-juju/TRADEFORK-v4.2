"""Q2 채팅 엔진 — TRADEFORK 핵심.

process_message()가 모든 채팅을 처리:
1. Intelligence 컨텍스트 구축
2. LLM 호출 (Sonnet) — 프롬프트 캐싱
3. 응답 파싱 (텍스트 + FORKER_META)
4. 메타데이터 기반 후처리
5. DB 저장
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    BaseStream,
    ChatMessage,
    User,
    UserTrigger,
)
from src.intelligence.episode import (
    build_intelligence_context,
    create_episode,
)
from src.llm.client import CACHE_CONTROL_EPHEMERAL, LLMClient, llm_client
from src.llm.prompts import (
    CHAT_CONTEXT_TEMPLATE,
    CHAT_SYSTEM_PROMPT_STATIC,
    SEARCH_RESPONSE_PROMPT,
)

logger = logging.getLogger(__name__)

# FORKER_META 파싱 정규식 — LLM 출력 포맷 변형에 대응하도록 유연하게
_META_PATTERN = re.compile(
    r"<!--\s*FORKER_META\s*(.*?)\s*FORKER_META\s*-->",
    re.DOTALL,
)


# ------------------------------------------------------------------
# DTO
# ------------------------------------------------------------------
@dataclass
class ChatResult:
    """process_message 반환 값."""

    response_text: str
    intent: str = "general"
    meta: dict[str, Any] = field(default_factory=dict)
    search_needed: bool = False




# ------------------------------------------------------------------
# 응답 파싱
# ------------------------------------------------------------------
def _parse_response(raw_text: str) -> tuple[str, dict[str, Any]]:
    """LLM 응답에서 텍스트와 FORKER_META를 분리.

    Returns:
        (유저에게 보여줄 텍스트, 메타데이터 dict)
    """
    meta: dict[str, Any] = {}

    match = _META_PATTERN.search(raw_text)
    if match:
        # 텍스트 = META 앞까지
        user_text = raw_text[: match.start()].strip()
        meta_str = match.group(1).strip()

        # JSON 주석(// ...) 제거
        meta_str = re.sub(r"//.*?(?:\n|$)", "\n", meta_str)
        # 트레일링 콤마 제거
        meta_str = re.sub(r",\s*([}\]])", r"\1", meta_str)

        try:
            meta = json.loads(meta_str)
        except json.JSONDecodeError:
            logger.warning("FORKER_META JSON 파싱 실패:\n%s", meta_str[:500])
            # 파싱 실패해도 텍스트 응답은 정상 전달 (graceful fallback)
    else:
        user_text = raw_text.strip()
        logger.debug("FORKER_META 없음 — 일반 텍스트로 처리")

    # 빈 텍스트 방지 — META만 있고 앞의 텍스트가 비어 있을 경우
    if not user_text:
        user_text = raw_text.strip()
        # META 블록까지 포함된 원본에서 HTML 주석 제거
        user_text = re.sub(r"<!--.*?-->", "", user_text, flags=re.DOTALL).strip()
        if not user_text:
            user_text = "응답을 생성하는데 문제가 있었어. 다시 말해줘!"

    return user_text, meta


# ------------------------------------------------------------------
# 후처리
# ------------------------------------------------------------------
async def _post_process(
    session: AsyncSession,
    user: User,
    meta: dict[str, Any],
    user_text: str,
    message_text: str,
) -> None:
    """FORKER_META 기반 후처리 — 트리거/에피소드/Base/캘리브레이션 등."""
    intent = meta.get("intent", "general")

    # a-0) patrol_deferred 안전장치: trigger_action이 없으면 자동 생성
    if intent == "patrol_deferred" and not meta.get("trigger_action"):
        meta["trigger_action"] = {
            "type": "llm_evaluated",
            "eval_prompt": message_text[:500],
            "data_needed": ["general"],
            "description": message_text[:200],
        }
        logger.info("patrol_deferred 자동 트리거 생성: %s", message_text[:50])

    # a) trigger_action → UserTrigger 생성
    trigger = meta.get("trigger_action")
    if trigger and isinstance(trigger, dict):
        trigger_type = trigger.get("type", "alert")
        ut = UserTrigger(
            user_id=user.id,
            trigger_type=trigger_type,
            condition=trigger.get("condition"),
            composite_logic=trigger.get("logic") or trigger.get("composite_logic"),
            base_streams_needed=trigger.get("base_streams_needed"),
            eval_prompt=trigger.get("eval_prompt"),
            data_needed=trigger.get("data_needed"),
            description=trigger.get("description", message_text[:200]),
            source="user_request",
        )
        session.add(ut)
        logger.info(
            "트리거 생성: user=%s, type=%s, desc=%s",
            user.telegram_id,
            trigger_type,
            ut.description[:50],
        )

        # signal 타입이면 필요한 Base 스트림도 추가
        streams_needed = trigger.get("base_streams_needed") or []
        for stream_info in streams_needed:
            if isinstance(stream_info, dict):
                bs = BaseStream(
                    user_id=user.id,
                    stream_type=stream_info.get("stream_type", "custom"),
                    symbol=stream_info.get("symbol") or stream_info.get("source"),
                    config=stream_info,
                    temperature="hot",
                )
                session.add(bs)

    # b) base_addition → BaseStream 추가
    base_add = meta.get("base_addition")
    if base_add and isinstance(base_add, dict):
        bs = BaseStream(
            user_id=user.id,
            stream_type=base_add.get("stream_type", "custom"),
            symbol=base_add.get("symbol"),
            config=base_add,
            temperature="hot",
        )
        session.add(bs)
        logger.info("Base 스트림 추가: %s/%s", bs.stream_type, bs.symbol)

    # c) should_save_episode → 에피소드 생성
    if meta.get("should_save_episode"):
        summary = meta.get("episode_summary", message_text[:200])
        await create_episode(
            session,
            user,
            episode_type="chat",
            user_action=message_text[:500],
            embedding_text=summary,
            reasoning=user_text[:500],
        )

    # d) calibration → 에피소드에 expression_calibration 추가
    cal = meta.get("calibration")
    if cal and isinstance(cal, dict):
        await create_episode(
            session,
            user,
            episode_type="chat",
            user_action=f"캘리브레이션: {cal.get('expression', '')}",
            embedding_text=f"표현 '{cal.get('expression')}' = {cal.get('actual_value')}",
            expression_calibration=cal,
        )

    # e) style_update → User.style_parsed 업데이트
    style = meta.get("style_update")
    if style and isinstance(style, dict):
        current = user.style_parsed or {}
        current.update(style)
        user.style_parsed = current
        logger.info("스타일 업데이트: %s", style)

    await session.flush()


# ------------------------------------------------------------------
# 메인 엔트리
# ------------------------------------------------------------------
async def process_message(
    session: AsyncSession,
    user: User,
    message_text: str,
    image_data: bytes | None = None,
    image_media_type: str = "image/jpeg",
) -> ChatResult:
    """Q2 채팅 메시지 처리 — 전체 파이프라인.

    Args:
        session: DB 세션
        user: 유저 객체
        message_text: 메시지 텍스트
        image_data: 이미지 바이트 (차트 분석용)
        image_media_type: 이미지 MIME 타입

    Returns:
        ChatResult (응답 텍스트 + 메타데이터)
    """

    # 1) Intelligence 컨텍스트 구축 (중앙 집중 — episode.py)
    ctx = await build_intelligence_context(session, user, message_text)

    # 2) 시스템 프롬프트 조립 — 2블록 분리로 캐싱 효율 극대화
    #    Block 1: 정적 (FORKER 정체성 + 응답 규칙) → 캐싱 O (모든 유저/메시지 동일)
    #    Block 2: 동적 (Intelligence, 원칙, Base, 포지션, 최근대화) → 캐싱 X
    dynamic_context = CHAT_CONTEXT_TEMPLATE.format(**ctx)

    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": CHAT_SYSTEM_PROMPT_STATIC,
            "cache_control": CACHE_CONTROL_EPHEMERAL,
        },
        {
            "type": "text",
            "text": dynamic_context,
        },
    ]

    # 3) 메시지 구성
    messages: list[dict[str, Any]] = []

    if image_data:
        # Vision 모드: 이미지 + 텍스트
        content_blocks = LLMClient.build_image_content(
            image_data=image_data,
            media_type=image_media_type,
            prompt=message_text or "이 차트를 분석해줘.",
        )
        messages.append({"role": "user", "content": content_blocks})
    else:
        messages.append({"role": "user", "content": message_text})

    # 4) LLM 호출 (Sonnet)
    try:
        resp = await llm_client.chat(
            system=system_blocks,
            messages=messages,
            max_tokens=4096,
        )
        raw_text = resp.text
        logger.info(
            "채팅 LLM: in=%d (cache_read=%d, cache_create=%d), out=%d",
            resp.input_tokens,
            resp.cache_read_tokens,
            resp.cache_creation_tokens,
            resp.output_tokens,
        )
    except Exception:
        logger.error("채팅 LLM 호출 실패", exc_info=True)
        return ChatResult(
            response_text="잠깐 문제가 생겼어. 다시 말해줘!",
            intent="general",
        )

    # 5) 응답 파싱
    user_text, meta = _parse_response(raw_text)
    intent = meta.get("intent", "general")

    # 6) market_question → 자율 서치 + 2차 응답
    if intent == "market_question":
        try:
            from src.data.search import autonomous_search

            search_results = await autonomous_search(
                query=message_text,
                user_language=user.language or "ko",
            )

            if search_results and search_results != "검색 결과 없음":
                # 검색 결과로 2차 LLM 호출
                search_prompt = SEARCH_RESPONSE_PROMPT.format(
                    question=message_text,
                    search_results=search_results,
                    intelligence_context=ctx["intelligence_context"],
                )
                search_resp = await llm_client.chat(
                    system=[{
                        "type": "text",
                        "text": search_prompt,
                        "cache_control": CACHE_CONTROL_EPHEMERAL,
                    }],
                    messages=[{"role": "user", "content": message_text}],
                    max_tokens=4096,
                )
                search_text, search_meta = _parse_response(search_resp.text)
                # 서치 응답의 메타로 업데이트
                if search_meta:
                    meta.update(search_meta)
                user_text = search_text
                logger.info(
                    "서치 2차 응답: in=%d, out=%d",
                    search_resp.input_tokens,
                    search_resp.output_tokens,
                )
        except Exception:
            logger.error("자율 서치 실패", exc_info=True)
            # 서치 실패해도 1차 응답은 유지

    # 7) 후처리
    await _post_process(session, user, meta, user_text, message_text)

    # 8) assistant 메시지 DB 저장
    session.add(
        ChatMessage(
            user_id=user.id,
            role="assistant",
            content=user_text,
            message_type="text",
            intent=intent,
            metadata_=meta if meta else None,
        )
    )
    await session.flush()

    return ChatResult(
        response_text=user_text,
        intent=intent,
        meta=meta,
        search_needed=False,  # 이미 처리됨
    )
