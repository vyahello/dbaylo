"""Shared Telegram upload download with the Bot API size ceiling handled.

The cloud Bot API lets a bot download files of at most 20 MB (``getFile``). A bigger upload — e.g.
a 31 MB scanned hospital discharge PDF — makes ``bot.download`` raise ``TelegramBadRequest: file is
too big``; uncaught, the handler died and the user got NO reply at all. This helper checks the
declared size first (no wasted round-trip) and also catches the server error as the final net, so
the user always gets a clear Ukrainian "too big — here is how to send it" answer.
"""

from __future__ import annotations

from io import BytesIO

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from dbaylo import locale

# Telegram cloud Bot API getFile limit (a local Bot API server would lift it; not deployed).
TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024


def is_too_big(file_size: int | None) -> bool:
    """Whether a declared upload size exceeds what the bot can download (unknown → try it)."""
    return file_size is not None and file_size > TELEGRAM_DOWNLOAD_LIMIT


def _too_big_text(file_size: int | None) -> str:
    size = f"{file_size / (1024 * 1024):.0f} МБ" if file_size else "понад 20 МБ"
    return locale.UPLOAD_TOO_BIG.format(size=size)


async def download_upload(message: Message, file_id: str, *, file_size: int | None) -> bytes | None:
    """Download an upload's bytes, or answer the "too big" message and return ``None``."""
    assert message.bot is not None
    if is_too_big(file_size):
        await message.answer(_too_big_text(file_size))
        return None
    buffer = BytesIO()
    try:
        await message.bot.download(file_id, destination=buffer)
    except TelegramBadRequest as exc:
        if "too big" not in str(exc).lower():
            raise
        await message.answer(_too_big_text(file_size))
        return None
    return buffer.getvalue()
