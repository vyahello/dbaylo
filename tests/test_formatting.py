"""Tests for the lab-interpretation Telegram HTML renderer (bot.formatting)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dbaylo import locale
from dbaylo.bot.formatting import (
    TELEGRAM_MAX,
    answer_chunked,
    render_interpretation_html,
    split_for_telegram,
)
from dbaylo.triage.safety import DISCLAIMER

_BODY = (
    f"{locale.INTERPRET_SECTION_OVERALL}\n"
    "Аналіз об'єднує два типи досліджень.\n\n"
    f"{locale.INTERPRET_SECTION_ATTENTION}\n"
    "• Лейкоцити в сечі — 11,25 при нормі 0–2.\n\n"
    f"{locale.INTERPRET_SECTION_HELP}\n"
    "• Більше води.\n\n"
    f"{locale.INTERPRET_SECTION_DOCTOR}\n"
    "• Якщо симптоми триватимуть."
)


def _rendered() -> str:
    return render_interpretation_html(f"{_BODY}\n\n{DISCLAIMER}")


def test_known_headers_become_bold_with_emoji() -> None:
    out = _rendered()
    assert f"🩺 <b>{locale.INTERPRET_SECTION_OVERALL}</b>" in out
    assert f"⚠️ <b>{locale.INTERPRET_SECTION_ATTENTION}</b>" in out
    assert f"🌿 <b>{locale.INTERPRET_SECTION_HELP}</b>" in out
    assert f"🧑‍⚕️ <b>{locale.INTERPRET_SECTION_DOCTOR}</b>" in out
    # The plain header text must not survive on its own line (it is replaced by the styled one).
    assert f"\n{locale.INTERPRET_SECTION_OVERALL}\n" not in out


def test_disclaimer_set_off_as_single_italic_ps() -> None:
    out = _rendered()
    assert out.count(DISCLAIMER) == 1  # no double disclaimer
    assert f"{locale.INTERPRET_PS_PREFIX} <i>{DISCLAIMER}</i>" in out
    assert locale.INTERPRET_DIVIDER in out
    # The disclaimer is the tail of the message.
    assert out.rstrip().endswith("</i>")


def test_body_is_html_escaped_so_a_stray_angle_bracket_cannot_break_parsing() -> None:
    # A real lab value can read "< 5"; HTML mode must see it escaped, never as a tag.
    text = f"{locale.INTERPRET_SECTION_OVERALL}\n• Лейкоцити < 5 & в нормі.\n\n{DISCLAIMER}"
    out = render_interpretation_html(text)
    assert "&lt; 5 &amp; в нормі" in out
    assert "< 5" not in out


def test_apostrophe_is_not_entity_encoded() -> None:
    # quote=False: the Ukrainian apostrophe in "об'єднує" must stay literal, not &#x27;.
    out = _rendered()
    assert "об'єднує" in out
    assert "&#x27;" not in out


def test_unknown_header_degrades_to_plain_text() -> None:
    text = f"Якийсь свій заголовок\nтекст.\n\n{DISCLAIMER}"
    out = render_interpretation_html(text)
    assert "Якийсь свій заголовок" in out
    assert "<b>" not in out.split(locale.INTERPRET_DIVIDER)[0]  # nothing bolded in the body


def test_deterministic_fallback_attention_header_is_styled() -> None:
    text = f"{locale.LAB_INTERPRET_FLAGGED_HEADER}\n• Глюкоза: 7\n\n{DISCLAIMER}"
    out = render_interpretation_html(text)
    assert f"⚠️ <b>{locale.INTERPRET_SECTION_ATTENTION}</b>" in out


# --- Long-message chunking (Telegram's 4096-char cap) ---------------------------


def test_short_text_is_one_chunk() -> None:
    assert split_for_telegram("just a line") == ["just a line"]


def test_a_big_report_splits_under_the_limit_on_line_boundaries() -> None:
    # ~85 rows like a real comprehensive panel — one single message would be rejected.
    body = "\n".join(f"{i}. Аналіт {i} — {i * 1.5:g} (норма 0–10) ⚠️" for i in range(1, 86)) * 3
    chunks = split_for_telegram(body)
    assert len(chunks) > 1
    assert all(len(c) <= TELEGRAM_MAX for c in chunks)
    # Reassembling the chunks reproduces every line (nothing dropped, no row cut in half).
    assert "\n".join(chunks).split("\n") == body.split("\n")


def test_an_overlong_single_line_is_hard_split() -> None:
    chunks = split_for_telegram("x" * 9000)
    assert len(chunks) >= 3
    assert all(len(c) <= TELEGRAM_MAX for c in chunks)
    assert "".join(chunks) == "x" * 9000


def test_split_breaks_between_panels_never_orphaning_a_header() -> None:
    # Two panels, each ~3 kB, so together they must split — but BETWEEN sections, not mid-panel.
    blood = "▸ Загальний аналіз крові\n" + "\n".join(
        f"{i}. показник крові номер {i} — 1.0" for i in range(1, 101)
    )
    urine = "▸ Загальний аналіз сечі\n" + "\n".join(
        f"{i}. показник сечі номер {i} — не виявлено" for i in range(1, 101)
    )
    chunks = split_for_telegram(f"{blood}\n\n{urine}")
    assert len(chunks) > 1
    # No chunk ends on a bare panel header (the bug: header in one message, rows in the next).
    assert all(not c.split("\n")[-1].lstrip().startswith("▸") for c in chunks)
    # Each panel header travels in the same message as its first row.
    blood_chunk = next(c for c in chunks if "▸ Загальний аналіз крові" in c)
    urine_chunk = next(c for c in chunks if "▸ Загальний аналіз сечі" in c)
    assert "1. показник крові номер 1" in blood_chunk
    assert "1. показник сечі номер 1" in urine_chunk


@pytest.mark.asyncio
async def test_answer_chunked_attaches_markup_only_to_the_last_chunk() -> None:
    message = AsyncMock()
    long_text = "\n".join("рядок" * 200 for _ in range(60))  # > one message
    markup = object()
    await answer_chunked(message, long_text, reply_markup=markup)  # type: ignore[arg-type]
    calls = message.answer.await_args_list
    assert len(calls) > 1
    assert all(c.kwargs["reply_markup"] is None for c in calls[:-1])
    assert calls[-1].kwargs["reply_markup"] is markup
