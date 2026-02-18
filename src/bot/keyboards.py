"""텔레그램 인라인 키보드 빌더."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def exchange_selection() -> InlineKeyboardMarkup:
    """온보딩 — 거래소 선택 키보드."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("바이낸스", callback_data="ex:binance"),
                InlineKeyboardButton("업비트", callback_data="ex:upbit"),
                InlineKeyboardButton("빗썸", callback_data="ex:bithumb"),
            ],
            [InlineKeyboardButton("등록 완료 →", callback_data="ex:done")],
        ]
    )


def confirm_reasoning() -> InlineKeyboardMarkup:
    """매매 근거 확인 — 맞아 / 아니야."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("맞아 ✅", callback_data="reason:yes"),
                InlineKeyboardButton("아니야 ❌", callback_data="reason:no"),
            ]
        ]
    )


def signal_feedback() -> InlineKeyboardMarkup:
    """시그널 피드백 — 동의 / 아닌 거 같아."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("동의 ✅", callback_data="sig:agree"),
                InlineKeyboardButton("아닌 거 같아 ❌", callback_data="sig:disagree"),
            ]
        ]
    )


def add_more_exchange() -> InlineKeyboardMarkup:
    """거래소 추가 등록 / 완료."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("다른 거래소 추가", callback_data="ex:more"),
                InlineKeyboardButton("등록 완료 →", callback_data="ex:done"),
            ]
        ]
    )
