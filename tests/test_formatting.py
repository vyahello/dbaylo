"""Tests for the lab-interpretation Telegram HTML renderer (bot.formatting)."""

from __future__ import annotations

from dbaylo import locale
from dbaylo.bot.formatting import render_interpretation_html
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
