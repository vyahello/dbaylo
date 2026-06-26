"""Shared keyboard builders (Tier 1.3 button menu).

A leaf module: it imports only ``locale`` and the aiogram-free callback tokens, so
every flow that opens a dialog can attach the shared cancel button, and ``menu_flow``
can build the persistent reply keyboard — without import cycles between the flows.
"""

from __future__ import annotations

import contextlib

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from dbaylo import locale
from dbaylo.companion import callbacks


async def clear_inline_keyboard(callback: CallbackQuery) -> None:
    """Remove the inline keyboard from the message a ONE-SHOT callback fired on.

    Terminal actions (confirm, delete, cancel, an offer choice, turn-off) must not leave their
    buttons tappable: otherwise a user can delete *and* then cancel the same message, or fire an
    offer twice, getting contradictory or duplicated replies. Best-effort — a stale/uneditable
    message just raises ``TelegramBadRequest``, which we ignore.
    """
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(reply_markup=None)


async def remove_button_row(callback: CallbackQuery) -> None:
    """Remove only the keyboard ROW that holds the tapped button; leave the rest tappable.

    For a single-row keyboard (e.g. a ``/problems`` item's [Вирішено][Перейменувати]) this clears
    it. For a BATCHED message (one "✅ <name>" per row) it consumes just that one concern, so the
    others stay actionable. Best-effort — a stale/uneditable message is ignored.
    """
    message = callback.message
    if not isinstance(message, Message) or message.reply_markup is None:
        return
    rows = [
        row
        for row in message.reply_markup.inline_keyboard
        if all(button.callback_data != callback.data for button in row)
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    with contextlib.suppress(TelegramBadRequest):
        await message.edit_reply_markup(reply_markup=markup)


# Persistent reply keyboard: ~5 agent-driven sections. 🩺 Моє здоровʼя leads (it aggregates
# analyses · problems · goals · check-in); 💊 Ліки й нагадування bundles meds + reminders.
_MENU_ROWS = (
    (locale.MENU_HEALTH,),
    (locale.MENU_CARE, locale.MENU_PRICES),
    (locale.MENU_MEMORY, locale.MENU_HELP),
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """The always-visible reply keyboard shown from /start onward."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in _MENU_ROWS],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    """A single ✖️ Скасувати button — attached to every FSM dialog prompt so a dialog
    is always escapable without typing (one shared CANCEL_DIALOG handler)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=locale.BTN_DIALOG_CANCEL, callback_data=callbacks.CANCEL_DIALOG
                )
            ]
        ]
    )


def section_keyboard(*buttons: tuple[str, str]) -> InlineKeyboardMarkup:
    """A section screen's inline actions, one button per row: (label, callback_data)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data)] for label, data in buttons
        ]
    )


# ❓ Довідка quick-jumps: two per row, straight into the agent screens — so the help is actionable
# ("tap a section"), not a list of "/" commands to memorise. Reuses the existing leaf callbacks.
_HELP_JUMPS = (
    (
        (locale.BTN_MENU_ANALYSES, callbacks.MENU_OPEN_ANALYSES),
        (locale.BTN_MENU_PROBLEMS, callbacks.MENU_PROB_LIST),
    ),
    (
        (locale.BTN_MENU_GOALS, callbacks.MENU_OPEN_GOALS),
        (locale.BTN_MENU_CHECKIN, callbacks.MENU_OPEN_CHECKIN),
    ),
    (
        (locale.BTN_MENU_MED_LIST, callbacks.MENU_MED_LIST),
        (locale.BTN_MENU_REMINDERS, callbacks.MENU_OPEN_REMINDERS),
    ),
    (
        (locale.BTN_MENU_PRICE, callbacks.MENU_PRICE),
        (locale.MENU_MEMORY, callbacks.MENU_OPEN_MEMORY),
    ),
)


def help_keyboard() -> InlineKeyboardMarkup:
    """Inline quick-jumps under ❓ Довідка: tap straight into a section's agent screen."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in _HELP_JUMPS
        ]
    )
