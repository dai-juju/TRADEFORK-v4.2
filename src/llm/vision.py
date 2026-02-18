"""차트 이미지 분석 — Claude Vision."""

from __future__ import annotations

import logging
from typing import Any

from src.llm.client import LLMClient, llm_client
from src.llm.prompts import CHART_ANALYSIS_PROMPT

logger = logging.getLogger(__name__)


async def analyze_chart_image(
    image_data: bytes,
    media_type: str = "image/jpeg",
    user_context: str = "",
    client: LLMClient | None = None,
) -> str:
    """차트 이미지를 Claude Vision으로 분석.

    Args:
        image_data: 이미지 바이트 데이터
        media_type: MIME 타입 (image/jpeg, image/png 등)
        user_context: 유저 컨텍스트 (원칙, 스타일, 포지션 등)
        client: LLM 클라이언트 (테스트용 주입)

    Returns:
        분석 텍스트
    """
    _client = client or llm_client

    system_prompt = CHART_ANALYSIS_PROMPT.format(
        user_context=user_context or "없음",
    )

    content_blocks = LLMClient.build_image_content(
        image_data=image_data,
        media_type=media_type,
        prompt="이 차트를 분석해줘.",
    )

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": content_blocks},
    ]

    try:
        resp = await _client.chat(
            system=system_prompt,
            messages=messages,
            max_tokens=2048,
        )
        logger.info(
            "차트 분석 완료: input=%d, output=%d tokens",
            resp.input_tokens,
            resp.output_tokens,
        )
        return resp.text
    except Exception:
        logger.error("차트 분석 실패", exc_info=True)
        return "차트 분석에 실패했어. 이미지가 선명한지 확인해봐!"
