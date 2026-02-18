"""Pinecone 벡터 스토어 — 에피소드 임베딩/유사 검색.

인덱스: tradefork-episodes (dim=1024, cosine)
임베딩: Pinecone inference — multilingual-e5-large
네임스페이스: user_{telegram_id} (유저별 격리)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pinecone import Pinecone

from src.config import PINECONE_API_KEY, PINECONE_INDEX_NAME

logger = logging.getLogger(__name__)

EMBED_MODEL = "multilingual-e5-large"
METADATA_TEXT_LIMIT = 1000  # Pinecone metadata에 저장할 텍스트 최대 길이


class VectorStore:
    """Pinecone 벡터 스토어 — lazy 초기화, 유저별 namespace 격리."""

    def __init__(self) -> None:
        self._pc: Pinecone | None = None
        self._index: Any = None

    def _ensure_init(self) -> None:
        """Pinecone 클라이언트 + 인덱스 lazy 초기화."""
        if self._pc is not None:
            return

        api_key = PINECONE_API_KEY.strip()
        if not api_key:
            raise RuntimeError("PINECONE_API_KEY 환경변수가 설정되지 않았습니다")

        self._pc = Pinecone(api_key=api_key)
        self._index = self._pc.Index(PINECONE_INDEX_NAME)
        logger.info("Pinecone 연결 완료 (index=%s)", PINECONE_INDEX_NAME)

    @staticmethod
    def _namespace(telegram_id: int) -> str:
        return f"user_{telegram_id}"

    # ---- 임베딩 ------------------------------------------------

    def _embed_sync(
        self, texts: list[str], input_type: str = "passage"
    ) -> list[list[float]]:
        """동기 임베딩 생성 (asyncio.to_thread 에서 호출)."""
        assert self._pc is not None
        result = self._pc.inference.embed(
            model=EMBED_MODEL,
            inputs=texts,
            parameters={"input_type": input_type},
        )
        return [item.values for item in result]

    # ---- public API (async) ------------------------------------

    async def upsert_episode(
        self,
        telegram_id: int,
        episode_id: int,
        text: str,
    ) -> str:
        """에피소드 벡터를 Pinecone에 upsert.

        Args:
            telegram_id: 텔레그램 user_id (namespace 구분용)
            episode_id: DB Episode.id
            text: 임베딩 대상 텍스트 (시장상황 + 유저행동 + 근거 + 결과)

        Returns:
            pinecone_id (Episode.pinecone_id에 저장할 값)
        """
        pinecone_id = f"ep_{episode_id}"

        def _sync() -> str:
            self._ensure_init()
            embeddings = self._embed_sync([text])
            self._index.upsert(
                vectors=[
                    {
                        "id": pinecone_id,
                        "values": embeddings[0],
                        "metadata": {
                            "episode_id": episode_id,
                            "text": text[:METADATA_TEXT_LIMIT],
                        },
                    }
                ],
                namespace=self._namespace(telegram_id),
            )
            return pinecone_id

        try:
            result = await asyncio.to_thread(_sync)
            logger.debug(
                "Pinecone upsert: user=%s, episode=%s", telegram_id, episode_id
            )
            return result
        except Exception:
            logger.error(
                "Pinecone upsert 실패: user=%s, episode=%s",
                telegram_id,
                episode_id,
                exc_info=True,
            )
            raise

    async def search_similar(
        self,
        telegram_id: int,
        query: str,
        top_k: int = 5,
    ) -> list[int]:
        """유사 에피소드 검색.

        Args:
            telegram_id: 텔레그램 user_id
            query: 검색 쿼리 텍스트
            top_k: 반환할 최대 결과 수

        Returns:
            유사 에피소드 ID 목록 (score 내림차순)
        """

        def _sync() -> list[int]:
            self._ensure_init()
            query_embedding = self._embed_sync([query], input_type="query")
            results = self._index.query(
                vector=query_embedding[0],
                top_k=top_k,
                namespace=self._namespace(telegram_id),
                include_metadata=True,
            )
            episode_ids: list[int] = []
            for match in results.matches:
                eid = match.metadata.get("episode_id") if match.metadata else None
                if eid is not None:
                    episode_ids.append(int(eid))
            return episode_ids

        try:
            return await asyncio.to_thread(_sync)
        except Exception:
            logger.error(
                "Pinecone 검색 실패: user=%s", telegram_id, exc_info=True
            )
            return []

    async def delete_episode(
        self,
        telegram_id: int,
        episode_id: int,
    ) -> None:
        """에피소드 벡터 삭제."""
        pinecone_id = f"ep_{episode_id}"

        def _sync() -> None:
            self._ensure_init()
            self._index.delete(
                ids=[pinecone_id],
                namespace=self._namespace(telegram_id),
            )

        try:
            await asyncio.to_thread(_sync)
            logger.debug(
                "Pinecone delete: user=%s, episode=%s", telegram_id, episode_id
            )
        except Exception:
            logger.error(
                "Pinecone 삭제 실패: user=%s, episode=%s",
                telegram_id,
                episode_id,
                exc_info=True,
            )
            raise


# 싱글톤 인스턴스
vector_store = VectorStore()
