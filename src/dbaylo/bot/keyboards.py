"""Shared keyboard builders (Tier 1.3 button menu).

A leaf module: it imports only ``locale`` and the aiogram-free callback tokens, so
every flow that opens a dialog can attach the shared cancel button, and ``menu_flow``
can build the persistent reply keyboard — without import cycles between the flows.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from dbaylo import locale
from dbaylo.companion import callbacks

# Persistent reply keyboard: two-per-row main actions, help on its own row.
_MENU_ROWS = (
    (locale.MENU_LABS, locale.MENU_GOALS),
    (locale.MENU_PROBLEMS, locale.MENU_MEDS),
    (locale.MENU_REMINDERS, locale.MENU_PRICES),
    (locale.MENU_HELP,),
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
