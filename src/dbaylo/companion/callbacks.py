"""Callback-data tokens shared between the proactive senders (companion) and the bot
callback handlers (bot/). Kept aiogram-free so companion never imports the bot layer.
"""

from __future__ import annotations

_SEP = ":"

PROBLEM_RESOLVE = "prob_resolve"
PROBLEM_RENAME = "prob_rename"
REMINDER_OFF = "rem_off"
MEDICATION_OFF = "med_off"


def _make(prefix: str, ident: int) -> str:
    return f"{prefix}{_SEP}{ident}"


def _parse(prefix: str, data: str) -> int | None:
    head, _, rest = data.partition(_SEP)
    return int(rest) if head == prefix and rest.isdigit() else None


def problem_resolve(condition_id: int) -> str:
    return _make(PROBLEM_RESOLVE, condition_id)


def parse_problem_resolve(data: str) -> int | None:
    return _parse(PROBLEM_RESOLVE, data)


def problem_rename(condition_id: int) -> str:
    return _make(PROBLEM_RENAME, condition_id)


def parse_problem_rename(data: str) -> int | None:
    return _parse(PROBLEM_RENAME, data)


def reminder_off(reminder_id: int) -> str:
    return _make(REMINDER_OFF, reminder_id)


def parse_reminder_off(data: str) -> int | None:
    return _parse(REMINDER_OFF, data)


def medication_off(medication_id: int) -> str:
    return _make(MEDICATION_OFF, medication_id)


def parse_medication_off(data: str) -> int | None:
    return _parse(MEDICATION_OFF, data)
