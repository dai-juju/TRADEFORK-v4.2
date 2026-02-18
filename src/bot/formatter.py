"""텔레그램 메시지 포매팅 헬퍼 — HTML parse_mode 사용."""

from __future__ import annotations

import html


def escape(text: str) -> str:
    """HTML 특수문자 이스케이프."""
    return html.escape(str(text))


def bold(text: str) -> str:
    return f"<b>{escape(text)}</b>"


def italic(text: str) -> str:
    return f"<i>{escape(text)}</i>"


def code(text: str) -> str:
    return f"<code>{escape(text)}</code>"


def pre(text: str) -> str:
    return f"<pre>{escape(text)}</pre>"
