"""Deterministic detection of a 'question about MY lab data' — for smart routing (#3).

A free-text turn that NAMES one of the indicators the user actually has data for AND reads like a
question/ask about it should be answered by the focused, indicator-grounded consultation (the deep
expert answer over that analyte's full history + the reminder/clinic affordances), not the general
companion. This module decides that, purely:

* :func:`is_data_question` — the text looks like a question/ask ABOUT something (so a bare statement
  that merely mentions an analyte does not hijack the chat into consult mode);
* :func:`match_indicator` — the text names one of the user's own analytes (stem match, tolerant of
  Ukrainian inflection, plus a few lay synonyms).

Pure: regex + string matching over the user's own indicators. NO LLM, NO DB, NO escalation — the
routed turn still goes through the gate inside ``companion.consult`` like every other turn, so this
adds no path to the model (the AST choke-point invariant is untouched).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for typing — no runtime import, so no module cycle
    from dbaylo.companion.health import HealthFinding

# Looks like a question / ask ABOUT something: a question mark, a Ukrainian interrogative, an ask
# verb, or a "results" noun. Combined with a concrete indicator match, this keeps a plain statement
# ("сьогодні їв печінку") from switching the chat into a consult.
_QUESTION_RE = re.compile(
    r"\?|\bчому\b|\bчом\b|\bщо\b|\bчи\b|\bяк(?:ий|а|е|і)?\b|\bнаскільки\b|\bзвідки\b|"
    r"розкаж|поясн|покаж|означа|"
    r"аналіз|показник|рівень|результат|норм|підвищ|знижен|висок|низьк|поган|турбу",
    re.IGNORECASE,
)

# Common lay synonyms -> the analyte-name stem they refer to.
_ALIASES: dict[str, str] = {
    "цукор": "глюкоз",
    "холестерол": "холестерин",
    "залізо в крові": "заліз",
}

# Trailing Ukrainian vowels / soft signs stripped to a stem, so "залізо" / "заліза" / "залізом" all
# reduce to "заліз" and match regardless of grammatical case.
_VOWELS = "аоеиіїєюяь'ʼ`"

_MIN_STEM = 4  # ignore short, ambiguous stems (e.g. "С", "рН")

# A pain / symptom complaint is NOT a question about lab data — it belongs to the symptom intake /
# OTC path, never a data-lookup consult. Without this, a body-part word in a complaint could collide
# with a same-stem analyte (e.g. headache "болі в голові" false-matching the spermogram "Патологія
# голови (еякулят)"). The intake router already runs first in the chat flow; this is belt-and-braces
# so the data-question matcher never steals a complaint even if it is reached directly.
_PAIN_SIGNAL_RE = re.compile(
    r"\bбол(?:ить|ять|ю|і|ів|іло|яч|ям|ями|ях)|\bбіль(?!ш)|знеболю|\bниє\b", re.IGNORECASE
)


def is_data_question(text: str) -> bool:
    """Whether the text reads like a question / ask about something (not a bare statement)."""
    return bool(_QUESTION_RE.search(text))


def _stem(word: str) -> str:
    return word.casefold().rstrip(_VOWELS)


def _name_stems(name: str) -> list[str]:
    """The matchable word stems of an analyte's name (its core, before any '(qualifier)')."""
    core = name.casefold().split("(", 1)[0]
    stems = [_stem(token) for token in re.split(r"[^\w]+", core) if token]
    return [s for s in stems if len(s) >= _MIN_STEM]


def match_indicator(text: str, findings: list[HealthFinding]) -> HealthFinding | None:
    """The indicator the user is asking about, or ``None``.

    Requires the text to read like a data question AND to name one of ``findings`` (by an analyte
    name stem, or a lay alias). ``findings`` is expected most-interesting first (see
    ``health.list_indicators``); on equal-length matches the earlier (more interesting) one wins.
    """
    if not is_data_question(text):
        return None
    if _PAIN_SIGNAL_RE.search(text):  # a pain complaint is intake/OTC territory, not a data lookup
        return None
    norm = text.casefold()
    alias_stems = {stem for lay, stem in _ALIASES.items() if lay in norm}
    best: HealthFinding | None = None
    best_len = 0
    for finding in findings:
        stems = _name_stems(finding.name)
        hits = [s for s in stems if s in norm or s in alias_stems]
        if not hits:
            continue
        longest = max(len(s) for s in hits)
        if longest > best_len:  # strict '>' keeps the first (most-interesting) on a tie
            best, best_len = finding, longest
    return best
