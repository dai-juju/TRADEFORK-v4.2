"""Anthropic LLM 클라이언트 — 모델 라우팅 + 프롬프트 캐싱 + Vision.

모델 라우팅 (Pro 요금제):
  chat / episode / patrol  → Sonnet 4.5
  signal_judge / trade_reasoning / onboarding_analysis → Opus 4.5

프롬프트 캐싱:
  system prompt에 cache_control={"type": "ephemeral"} 추가.
  Intelligence 컨텍스트 중 정적 부분(유저 프로필, 원칙, 스타일)도 캐싱.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import anthropic

from src.config import ANTHROPIC_API_KEY, MODEL_OPUS, MODEL_SONNET

logger = logging.getLogger(__name__)

CACHE_CONTROL_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


# ------------------------------------------------------------------
# Response DTO
# ------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LLMResponse:
    """LLM 호출 결과."""

    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    model: str
    stop_reason: str


# ------------------------------------------------------------------
# Client
# ------------------------------------------------------------------
class LLMClient:
    """Anthropic 비동기 클라이언트 — 모든 LLM 호출의 단일 진입점."""

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=ANTHROPIC_API_KEY or None,
            max_retries=3,
            timeout=120.0,
        )

    # ---- helpers ------------------------------------------------

    @staticmethod
    def _build_system_blocks(
        system: str | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """system 파라미터를 캐싱 가능한 content block 리스트로 변환.

        - str → 단일 cached block
        - list[dict] → 그대로 사용 (호출자가 cache_control 제어)
        """
        if isinstance(system, str):
            return [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": CACHE_CONTROL_EPHEMERAL,
                }
            ]
        return system

    @staticmethod
    def build_image_content(
        image_data: bytes,
        media_type: str = "image/jpeg",
        prompt: str = "",
    ) -> list[dict[str, Any]]:
        """이미지 + 텍스트 content 블록 생성 (Vision 메시지용).

        Usage::

            msg = {
                "role": "user",
                "content": LLMClient.build_image_content(png_bytes, "image/png", "차트 분석해줘"),
            }
        """
        blocks: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image_data).decode("ascii"),
                },
            },
        ]
        if prompt:
            blocks.append({"type": "text", "text": prompt})
        return blocks

    # ---- core call ---------------------------------------------

    async def _call(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Anthropic Messages API 호출 — 내부 공통 메서드."""
        system_blocks = self._build_system_blocks(system)

        try:
            resp = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=messages,
            )
        except anthropic.AuthenticationError:
            logger.error("Anthropic 인증 실패 — ANTHROPIC_API_KEY 확인 필요")
            raise
        except anthropic.RateLimitError:
            logger.warning("Anthropic rate limit 초과 (model=%s)", model)
            raise
        except anthropic.APITimeoutError:
            logger.warning("Anthropic 타임아웃 (model=%s)", model)
            raise
        except anthropic.APIError as exc:
            logger.error("Anthropic API 에러: status=%s", getattr(exc, "status_code", "?"))
            raise

        # 응답 텍스트 추출
        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text

        usage = resp.usage
        return LLMResponse(
            text=text,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            model=resp.model,
            stop_reason=resp.stop_reason,
        )

    # ---- Model-routed public methods ----------------------------
    # Sonnet 4.5: 채팅, 에피소드, Patrol
    # Opus 4.5:   시그널 판단, 매매 근거 추론, 온보딩 분석

    async def chat(
        self,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Q2 채팅 — Sonnet 4.5."""
        return await self._call(
            model=MODEL_SONNET,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )

    async def episode(
        self,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """에피소드 생성 — Sonnet 4.5."""
        return await self._call(
            model=MODEL_SONNET,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )

    async def patrol(
        self,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Patrol 분석 — Sonnet 4.5."""
        return await self._call(
            model=MODEL_SONNET,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )

    async def signal_judge(
        self,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Tier 3 시그널 판단 — Opus 4.5."""
        return await self._call(
            model=MODEL_OPUS,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )

    async def trade_reasoning(
        self,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Q1 매매 근거 추론 — Opus 4.5."""
        return await self._call(
            model=MODEL_OPUS,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )

    async def onboarding_analysis(
        self,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """온보딩 초기 분석 — Opus 4.5."""
        return await self._call(
            model=MODEL_OPUS,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
        )


# 싱글톤 인스턴스
llm_client = LLMClient()
