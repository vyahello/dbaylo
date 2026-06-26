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
import re

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

# Leading/trailing chrome a header line may carry (numbering, colon, dash, bullet, our markers).
_HEADER_CHROME = " \t:：.—–-•·*_"

# Panel sub-header prefix ("▸ ") used by the confirm/results/interpretation views. Defined here so
# the renderer can bold a panel label while keeping the literal "▸" outside the tag — the chunk
# splitter (``_is_section_start``) still recognises it as a section boundary.
_PANEL_PREFIX = locale.LAB_SECTION_HEADER.split("{", 1)[0].strip()

# Light inline markup the interpretation model may emit: *bold* and _italic_, for the analyte+value,
# small sub-headings, and gentle caveats. We convert these to HTML tags AFTER escaping the text, so
# the only angle brackets in the final string are the ones we inject — a stray '<' in lab data can
# never break Telegram's parser. Non-greedy and single-line, so an unbalanced marker stays literal.
_BOLD_RE = re.compile(r"\*([^*\n]+)\*")
_ITALIC_RE = re.compile(r"_([^_\n]+)_")
# A [text](https://url) markdown link — used by the clinic finder for sources / clinic sites.
# Telegram HTML mode does not interpret markdown, so without this it shows a literal "[text](url)".
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")


def _escape(text: str) -> str:
    # quote=False: this is message *body* text, never an attribute, so apostrophes/quotes
    # (e.g. "об'єднує") must pass through literally — &#x27; would render verbatim in Telegram.
    return html.escape(text, quote=False)


def _inline_markup(escaped: str) -> str:
    """Convert *bold* / _italic_ markers and [text](url) links to HTML. Run on already-escaped text;
    links last so the URL is not mangled by the italic rule."""
    out = _ITALIC_RE.sub(r"<i>\1</i>", _BOLD_RE.sub(r"<b>\1</b>", escaped))
    return _MD_LINK_RE.sub(r'<a href="\2">\1</a>', out)


def _match_header(line: str) -> tuple[str, str] | None:
    key = line.strip().strip(_HEADER_CHROME).strip().casefold()
    return _SECTIONS.get(key) if key else None


# --- Companion / intake text: tidy whatever markdown the LLM emitted into clean HTML -------------
# These two personas are meant to be plain/light, but the model sometimes slips in **double bold**,
# '# headings', '---' rules or backticks, which Telegram (no markdown mode) shows LITERALLY. We
# normalise them at the send site so the user never sees a stray '**' or '#'.
_BOLD2_RE = re.compile(r"\*\*([^*\n]+)\*\*")  # **double** bold
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")  # a "# Heading" line
_HR_RE = re.compile(r"^\s*([*_-])\1{2,}\s*$")  # a "---" / "***" / "___" divider line
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+")  # "- item" / "* item" / "+ item"


def _companion_inline(escaped: str) -> str:
    """Inline markup for casual companion text (run on already-escaped text): **bold**/*bold* ->
    <b>, _italic_ -> <i>, [text](url) -> link, and drop stray inline-code backticks."""
    out = _BOLD_RE.sub(r"<b>\1</b>", _BOLD2_RE.sub(r"<b>\1</b>", escaped))
    out = _ITALIC_RE.sub(r"<i>\1</i>", out).replace("`", "")
    return _MD_LINK_RE.sub(r'<a href="\2">\1</a>', out)


def _disclaimer_ps(*, full: bool) -> str:
    """The italic P.S. under a divider: the full disclaimer (first turn / one-shot) or the compact
    reminder (a continuation turn — the not-a-doctor framing stays, just shorter)."""
    text = DISCLAIMER if full else locale.DISCLAIMER_SHORT
    return f"{locale.INTERPRET_DIVIDER}\n{locale.INTERPRET_PS_PREFIX} <i>{_escape(text)}</i>"


def render_companion_html(text: str, *, full_disclaimer: bool = True) -> str:
    """Render a companion / intake reply as Telegram HTML, tidying any markdown: bold/italic become
    tags, a '# heading' becomes bold, '-' bullets become '•', and '---' rules / backticks are
    dropped. The trailing disclaimer is set off as the same italic P.S. under a divider as the lab
    reading (premium look) — compact when ``full_disclaimer`` is False (a continuation turn).
    Escapes first, so a stray '<' can never break Telegram's parser."""
    body = text
    if body.endswith(DISCLAIMER):
        body = body[: -len(DISCLAIMER)].rstrip()
    lines: list[str] = []
    for raw in body.splitlines():
        if _HR_RE.match(raw):
            continue  # drop a markdown divider line
        heading = _HEADING_RE.match(raw)
        if heading is not None:
            lines.append(f"<b>{_escape(heading.group(1)).replace('*', '').replace('_', '')}</b>")
            continue
        lines.append(_companion_inline(_escape(_BULLET_RE.sub(r"\1• ", raw))))
    out = "\n".join(lines).rstrip()
    if not text.endswith(DISCLAIMER):  # no disclaimer to set off
        return out
    return f"{out}\n\n{_disclaimer_ps(full=full_disclaimer)}"


def render_interpretation_html(text: str, *, full_disclaimer: bool = True) -> str:
    """Render the plain ``interpret()`` output (body + trailing ``DISCLAIMER``) as Telegram HTML.

    Section headers become bold + emoji; inline *bold*/_italic_ markers become tags. The disclaimer
    is set off as an italic P.S. under a divider (one disclaimer; the model adds none) — compact
    when ``full_disclaimer`` is False (a continuation turn).
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
        elif line.lstrip().startswith(_PANEL_PREFIX):
            # A "▸ Панель" sub-header: bold the label, keep the literal "▸" so the chunk splitter
            # still treats the line as a section boundary.
            label = line.lstrip()[len(_PANEL_PREFIX) :].strip()
            rendered.append(f"{_PANEL_PREFIX} <b>{_escape(label)}</b>")
        else:
            rendered.append(_inline_markup(_escape(line)))

    out = "\n".join(rendered).rstrip()
    return f"{out}\n\n{_disclaimer_ps(full=full_disclaimer)}"


# Stable order of the interpretation's sections, for navigable (drill-down) delivery.
SECTION_KEYS: tuple[str, ...] = ("overall", "attention", "help", "doctor")
_DISPLAY_TO_KEY: dict[str, str] = {
    locale.INTERPRET_SECTION_OVERALL.casefold(): "overall",
    locale.INTERPRET_SECTION_ATTENTION.casefold(): "attention",
    locale.INTERPRET_SECTION_HELP.casefold(): "help",
    locale.INTERPRET_SECTION_DOCTOR.casefold(): "doctor",
}


def split_interpretation(text: str) -> dict[str, str]:
    """Split a stored interpretation into ``{section_key: "Header\\n<body>"}`` for the four
    canonical sections, so each can be delivered on its own (drill-down).

    Sections that are absent or empty are omitted; the trailing disclaimer is dropped (each
    section re-adds the P.S. when rendered through ``render_interpretation_html``). If the text
    does not carry the canonical headers — a narrative reading, or the deterministic fallback —
    the result lacks the ``overall`` key, and the caller sends the whole thing as before.
    """
    body = text
    if body.endswith(DISCLAIMER):
        body = body[: -len(DISCLAIMER)].rstrip()
    buckets: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for line in body.splitlines():
        match = _match_header(line)
        key = _DISPLAY_TO_KEY.get(match[1].casefold()) if match is not None else None
        if key is not None:
            current = key
            if key not in buckets:
                buckets[key] = []
                order.append(key)
        if current is not None:
            buckets[current].append(line)
    return {k: "\n".join(buckets[k]).strip() for k in order if "\n".join(buckets[k]).strip()}


# A line that begins a section: a panel header ("▸ …") in the confirm/results view, or one of the
# interpretation's emoji headers ("🩺 <b>Загалом</b>", …). We split BETWEEN sections, never mid-
# section, so a header is never orphaned from its rows in a separate message. (Inline *bold* in a
# body line is NOT a section start — only these markers are.)
_SECTION_EMOJIS = tuple({emoji for emoji, _ in _SECTIONS.values()})


def _is_section_start(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(_PANEL_PREFIX) or stripped.startswith(_SECTION_EMOJIS)


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
