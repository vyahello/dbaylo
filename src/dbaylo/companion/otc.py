"""Deterministic allow-list of MINOR, self-limiting complaints for which Дбайло may mention general
over-the-counter (no-prescription) options — pure (no LLM/DB/network).

This is one half of the OTC gate (the OTHER half, the non-negotiable one, is the triage acuity:
OTC is offered ONLY at ``Action.MONITOR`` — a red flag escalates and shows no OTC). This list keeps
the offer to clearly minor, common complaints (headache, sore throat, cold, heartburn, minor ache);
fever / chest pain / kidney-flank / vomiting / bleeding and the like are deliberately ABSENT — they
are triage/serious territory, never self-care suggestions. Mirrors :mod:`companion.symptoms`.
"""

from __future__ import annotations

# Casefolded substrings of clearly minor, self-limiting complaints (apostrophe variants included for
# мʼязи, since users type both ʼ and '). Conservative by design — when in doubt, leave it OUT.
_OTC_AMENABLE: tuple[str, ...] = (
    "головн біль",
    "болить голова",
    "голова болить",
    "болі в голов",
    "болю в голов",
    "біль в голов",
    "біль у голов",
    "болить в голов",
    "голові болить",
    "мігрен",
    "болить горло",
    "горло болить",
    "біль у горлі",
    "першит",
    "нежит",
    "закладен ніс",
    "соплі",
    "застуд",
    "простуд",
    "кашель",
    "кашл",
    "печія",
    "печіє",
    "згага",
    "болять мʼязи",
    "болять м'язи",
    "біль у мʼязах",
    "біль у м'язах",
    "мʼязов біль",
    "ломота",
    "ломить тіло",
    "свербіж",
    "свербить",
    "алергі",
    "здуття",
    "метеоризм",
)


def otc_amenable(text: str) -> bool:
    """True when ``text`` is a clearly minor, common complaint people self-treat with OTC options.

    NOT sufficient on its own to offer OTC — the caller ALSO requires the triage acuity to be
    ``MONITOR`` (no red flag). This only keeps the offer off non-minor complaints.
    """
    low = (text or "").casefold()
    return any(keyword in low for keyword in _OTC_AMENABLE)
