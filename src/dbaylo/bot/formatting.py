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

from aiogram.types import InlineKeyboardMarkup, Message

from dbaylo import locale
from dbaylo.triage.safety import DISCLAIMER

# Telegram rejects a sendMessage over 4096 chars ("message is too long"). A big lab report
# (e.g. an 8-page panel with ~85 rows) easily exceeds that, so any potentially-large message
# is split into chunks first. We leave a margin under the hard cap.
TELEGRAM_MAX = 4096
_CHUNK_LIMIT = 3900

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


# A line that begins a section: a panel header ("▸ …") in the confirm/results view, or a bold
# header ("… <b>Загалом</b>") in the interpretation. We split BETWEEN sections, never mid-section,
# so a header is never orphaned from its rows in a separate message.
_PANEL_PREFIX = locale.LAB_SECTION_HEADER.split("{", 1)[0].strip()


def _is_section_start(line: str) -> bool:
    return line.lstrip().startswith(_PANEL_PREFIX) or "<b>" in line


def _section_blocks(text: str) -> list[str]:
    """Group lines into section blocks — each header line starts a new block, carrying its rows."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if _is_section_start(line) and current:
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block).rstrip() for block in blocks]


def _split_lines(text: str, limit: int) -> list[str]:
    """Pack whole lines into chunks ≤ ``limit``; hard-split only a single over-long line."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if current and len(current) + 1 + len(line) > limit:
            chunks.append(current)
            current = ""
        if len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(line), limit):
                piece = line[start : start + limit]
                if len(piece) == limit:
                    chunks.append(piece)
                else:
                    current = piece
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def split_for_telegram(text: str, *, limit: int = _CHUNK_LIMIT) -> list[str]:
    """Split ``text`` into chunks ≤ ``limit``, breaking on SECTION boundaries where possible.

    Each section (its ``▸``/``<b>`` header + its rows) is kept whole in one message; a new message
    starts before a section that would overflow. Only a single section larger than one whole
    message is line-split as a last resort (its header rides the first part). Splitting on newlines
    also keeps our one-line ``<b>``/``<i>`` tags intact, so an HTML message stays valid.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in _section_blocks(text):
        if len(block) > limit:  # a single section bigger than a message: flush, then line-split it
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_lines(block, limit))
            continue
        if current and len(current) + 2 + len(block) > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks


async def answer_chunked(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    """Send ``text`` as one or more messages, each under Telegram's length cap.

    The ``reply_markup`` is attached only to the LAST chunk, so the action buttons (confirm /
    edit, per-analyte trend) sit at the bottom of the final part — never lost to an overflow.
    """
    chunks = split_for_telegram(text)
    last = len(chunks) - 1
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            reply_markup=reply_markup if index == last else None,
            parse_mode=parse_mode,
        )
