"""Telegram presentation for the lab interpretation message.

The interpretation itself (``labs.humanize.interpret``) is produced as PLAIN, safety-checked
Ukrainian text and stored that way. The LLM is never allowed to emit markup: lab text is full of
``<``, ``&`` and ``—``, and a single stray angle bracket would break Telegram's HTML parser and
drop the entire message. So formatting is applied deterministically here, at the send site — the
whole body is HTML-escaped first, then we inject our OWN ``<b>`` / ``<i>`` tags around the fixed
section headers and the disclaimer. A header the model phrased slightly differently simply stays
plain; it never produces a broken send.
"""

from __future__ import annotations

import html

from dbaylo import locale
from dbaylo.triage.safety import DISCLAIMER

# Fixed section headers -> (emoji, canonical display). Keyed by a normalized (casefolded,
# punctuation-stripped) header so small variations — and the deterministic fallback header —
# still match.
_SECTIONS: dict[str, tuple[str, str]] = {
    locale.INTERPRET_SECTION_OVERALL.casefold(): ("🩺", locale.INTERPRET_SECTION_OVERALL),
    locale.INTERPRET_SECTION_ATTENTION.casefold(): ("⚠️", locale.INTERPRET_SECTION_ATTENTION),
    locale.INTERPRET_SECTION_HELP.casefold(): ("🌿", locale.INTERPRET_SECTION_HELP),
    locale.INTERPRET_SECTION_DOCTOR.casefold(): ("🧑‍⚕️", locale.INTERPRET_SECTION_DOCTOR),
    # The deterministic fallback prints "Варто звернути увагу на:" — fold it to the same header.
    locale.LAB_INTERPRET_FLAGGED_HEADER.strip(" \t:：.—–-•·").casefold(): (
        "⚠️",
        locale.INTERPRET_SECTION_ATTENTION,
    ),
}

# Leading/trailing chrome a header line may carry (numbering, colon, dash, bullet).
_HEADER_CHROME = " \t:：.—–-•·"


def _escape(text: str) -> str:
    # quote=False: this is message *body* text, never an attribute, so apostrophes/quotes
    # (e.g. "об'єднує") must pass through literally — &#x27; would render verbatim in Telegram.
    return html.escape(text, quote=False)


def _match_header(line: str) -> tuple[str, str] | None:
    key = line.strip().strip(_HEADER_CHROME).strip().casefold()
    return _SECTIONS.get(key) if key else None


def render_interpretation_html(text: str) -> str:
    """Render the plain ``interpret()`` output (body + trailing ``DISCLAIMER``) as Telegram HTML.

    Section headers become bold + emoji; the disclaimer is set off as an italic P.S. under a
    divider (a single disclaimer — the model is told not to add its own).
    """
    body = text
    if body.endswith(DISCLAIMER):
        body = body[: -len(DISCLAIMER)].rstrip()

    rendered: list[str] = []
    for line in body.splitlines():
        match = _match_header(line)
        if match is not None:
            emoji, display = match
            rendered.append(f"{emoji} <b>{_escape(display)}</b>")
        else:
            rendered.append(_escape(line))

    out = "\n".join(rendered).rstrip()
    ps = f"{locale.INTERPRET_DIVIDER}\n{locale.INTERPRET_PS_PREFIX} <i>{_escape(DISCLAIMER)}</i>"
    return f"{out}\n\n{ps}"
