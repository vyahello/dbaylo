"""Telegram webhook entrypoint (mirrors the Communal Butler pattern).

Updates arrive at ``POST /webhook/{token}``; the token in the path is checked
against the configured ``BOT_TOKEN`` so only Telegram can post here. The raw
update is fed to the same aiogram Dispatcher used by long polling, so handler
behaviour is identical across transports.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from dbaylo.bot.app import build_bot, build_dispatcher
from dbaylo.config import Settings, get_settings

router = APIRouter()

# Built lazily so importing the app never requires a token (e.g. for /health tests).
_dispatcher: Dispatcher | None = None
_bot: Bot | None = None


def _get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = build_dispatcher()
    return _dispatcher


def _get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = build_bot()
    return _bot


@router.post("/webhook/{token}")
async def telegram_webhook(
    token: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Receive a Telegram update and dispatch it to the bot handlers."""
    if not settings.bot_token or token != settings.bot_token:
        raise HTTPException(status_code=403, detail="invalid webhook token")

    update = Update.model_validate(await request.json(), context={"bot": _get_bot()})
    await _get_dispatcher().feed_update(bot=_get_bot(), update=update)
    return Response(status_code=200)
