"""자율 서치 — 시장 질문 시 최적 소스 검색.

검색 순서 (비용 최적화):
① Base 데이터 확인 (비용 0)
② 외부 API (CryptoPanic, CMC)
③ Tavily 웹 검색 (한국어 + 영어 쿼리 모두)
④ 필요시 Playwright 브라우징 (Phase 7에서 활성화)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from src.config import TAVILY_API_KEY

logger = logging.getLogger(__name__)


def _extract_symbols(query: str) -> list[str]:
    """쿼리에서 코인 심볼 추출."""
    # 대문자 2~6글자 패턴 (BTC, ETH, SOL, DOGE 등)
    symbols = re.findall(r"\b([A-Z]{2,6})\b", query.upper())
    # 흔한 비-심볼 단어 제거
    noise = {"WHY", "THE", "HOW", "WHAT", "WHEN", "AND", "FOR", "ARE", "BUT", "NOT"}
    return [s for s in symbols if s not in noise]


def _make_bilingual_queries(query: str, language: str) -> list[str]:
    """한국어/영어 쿼리 쌍 생성."""
    queries = [query]
    symbols = _extract_symbols(query)
    symbol_str = " ".join(symbols) if symbols else ""

    if language == "ko":
        # 한국어 → 영어 쿼리 추가
        if symbol_str:
            queries.append(f"{symbol_str} crypto price analysis why")
        else:
            queries.append(f"crypto {query} analysis")
    else:
        # 영어 → 한국어 쿼리 추가
        if symbol_str:
            queries.append(f"{symbol_str} 코인 분석 이유")
        else:
            queries.append(f"암호화폐 {query} 분석")

    return queries


async def _search_tavily(query: str) -> list[dict[str, Any]]:
    """Tavily API로 웹 검색."""
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY 미설정, 웹 검색 건너뜀")
        return []

    try:
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=TAVILY_API_KEY)
        response = await client.search(
            query=query,
            search_depth="advanced",
            max_results=5,
        )
        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0),
            })
        return results
    except Exception:
        logger.error("Tavily 검색 실패: %s", query, exc_info=True)
        return []


async def autonomous_search(
    query: str,
    user_language: str = "ko",
) -> str:
    """시장 질문 시 자율 검색 → 결과 텍스트 반환.

    Args:
        query: 유저 질문
        user_language: 유저 언어 ("ko" | "en")

    Returns:
        검색 결과를 종합한 텍스트
    """
    queries = _make_bilingual_queries(query, user_language)

    # Tavily 병렬 검색 (한국어 + 영어)
    tasks = [_search_tavily(q) for q in queries]
    all_results = await asyncio.gather(*tasks)

    # 결과 병합 + 중복 제거
    seen_urls: set[str] = set()
    merged: list[dict[str, Any]] = []
    for results in all_results:
        for item in results:
            url = item.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                merged.append(item)

    if not merged:
        return "검색 결과 없음"

    # 점수 기준 정렬
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)

    # 텍스트로 포맷
    lines: list[str] = []
    for i, item in enumerate(merged[:8], 1):
        title = item.get("title", "제목 없음")
        content = item.get("content", "")[:500]
        url = item.get("url", "")
        lines.append(f"[{i}] {title}\n{content}\n출처: {url}")

    return "\n\n".join(lines)
