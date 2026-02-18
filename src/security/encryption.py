"""AES 대칭 암호화 — 거래소 API 키 보호.

Fernet (AES-128-CBC + HMAC-SHA256) 기반.
ENCRYPTION_KEY 환경변수에서 키 로드.

절대 규칙:
 - 복호화된 값을 로그에 남기지 말 것
 - 복호화는 런타임 메모리에서만, 사용 후 즉시 폐기
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from src.config import ENCRYPTION_KEY

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Fernet 인스턴스를 lazy 초기화."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = ENCRYPTION_KEY.strip()
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY 환경변수가 설정되지 않았습니다. "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            "로 생성하세요."
        )
    try:
        _fernet = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except (ValueError, Exception) as exc:
        raise RuntimeError(f"ENCRYPTION_KEY 형식 오류: {exc}") from exc
    return _fernet


def encrypt(plaintext: str) -> bytes:
    """평문 문자열 → 암호화된 bytes. 거래소 API 키 저장용."""
    if not plaintext:
        raise ValueError("빈 문자열은 암호화할 수 없습니다")
    return _get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """암호화된 bytes → 복호화된 평문 문자열. 사용 후 즉시 폐기할 것."""
    if not ciphertext:
        raise ValueError("빈 ciphertext는 복호화할 수 없습니다")
    try:
        return _get_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken:
        logger.error("복호화 실패 — ENCRYPTION_KEY가 변경되었거나 데이터 손상")
        raise
