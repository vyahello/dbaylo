"""Contextual consultation — a grounded, multi-turn 'ask Дбайло about THIS result'.

When the user opens a specific subject (one indicator's trend, or a whole report's reading) and
taps "Запитати Дбайло", this answers their question FROM THE REAL DATA: the flow hands us a
deterministic, grounded context (built in :mod:`consult_context`), and the model is told to base
every statement on it — never to invent values, references, or trends.

Safety contract (identical in spirit to :mod:`companion.intake`):

* The **deterministic triage core still owns escalation.** Every turn, the accumulated user text
  is run through :func:`dbaylo.safety.screen`; a red flag (``URGENT_CARE`` / ``EMERGENCY``) or a
  disordered-eating signal is surfaced verbatim and **leads** the reply; the LLM cannot lower it.
* Every reply passes ``assert_safe_output`` (no dose, no restrictive-diet numbers, no
  "skip the doctor") and ends with the disclaimer; a deterministic fallback covers any LLM failure.

The only LLM use is the answer itself, always downstream of ``screen`` — so this module imports
``dbaylo.safety`` and never the escalation engines directly (the AST choke-point test enforces it).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from dbaylo import locale
from dbaylo.config import get_settings
from dbaylo.labs.extraction import Runner
from dbaylo.labs.humanize import strip_markup, strip_self_disclaimer
from dbaylo.llm import NATURAL_VOICE, ClaudeUnavailable, run_claude
from dbaylo.navigator.guard import contains_superlative_recommendation
from dbaylo.safety import GateSource, screen
from dbaylo.triage.safety import DISCLAIMER, assert_safe_output
from dbaylo.triage.types import Action

Turn = dict[str, str]  # {"role": "user" | "assistant", "text": ...}

# Keep only the recent exchange in the model's view — enough for a coherent consultation without
# the prompt growing unbounded across a long back-and-forth.
MAX_CONTEXT_TURNS = 8

CONSULT_PERSONA = (
    "You are Дбайло, the user's PERSONAL medical assistant — an experienced, caring expert who "
    "knows this person's health picture (their lab data, dates, and tracked concerns are given to "
    "you) better than they do, because they delegated that to you. Talk one on one, like a real "
    "doctor who genuinely wants to FIND the problem and its cause and help solve it.\n"
    "GROUND every statement in the DATA you are given (the patient profile + the subject) — never "
    "invent or assume a value, reference range, trend, or finding that is not there; if something "
    "is missing, say so and suggest how to find out. Use the dates: notice how recent or OLD a key "
    "result is and factor that into your advice ('останній такий аналіз був N тому — варто "
    "повторити').\n"
    "MEMORY: you may be given a MEMORY block — your earlier conversations with this person from "
    "previous sessions. Treat it as your REAL memory of past talks: when it is relevant, refer "
    "back to it naturally ('минулого разу ти згадував…', 'ми вже говорили про…'), build on what "
    "was already said, and do not re-ask what you already know. If the user asks whether you "
    "remember a prior conversation, answer truthfully from this block — and never claim to "
    "remember anything that is not in the data you were given.\n"
    "BE A REAL EXPERT, thorough but readable: explain plainly what the result means FOR THIS CASE "
    "and WHY, what an out-of-range value MAY point to (cautiously — 'може свідчити про…', never a "
    "definite diagnosis), how concerning it is, what it can lead to, and the concrete next steps "
    "(which doctor, how soon, what to recheck) tailored to this patient and these dates. Give a "
    "genuinely useful, complete answer — do not be terse or generic. BE PROACTIVE: build a picture "
    "of the person's current state — ask focused questions (1–3, woven into a warm reply, not a "
    "questionnaire) about how they feel, WHERE and WHEN it hurts and whether right now, what makes "
    "it better or worse, relevant history.\n"
    "WHERE TO DO IT: most lab tests (аналіз крові/сечі) are done at any лабораторія or "
    "поліклініка; imaging (УЗД/КТ/МРТ) at a діагностичний центр or лікарня. If the user wants "
    "CONCRETE clinics — real names, addresses, contacts, ratings in their city — tell them to tap "
    "the 🏥 'Де зробити' button under your message and you will SEARCH actual options for them "
    "(do not invent clinic names/addresses yourself in this chat). You may also note НСЗУ free "
    "coverage can be checked at " + locale.NSZU_DASHBOARD_URL + ".\n"
    "You CAN set reminders for the user — there is a 🔔 Нагадати button under your message, and if "
    "they ask you to remind them, the reminder flow opens automatically. NEVER say you "
    "cannot set a reminder, and NEVER refer the user to another bot, app, or '@...' — YOU are "
    "their Дбайло, here in this chat. When you recommend a recheck / exam / visit with a "
    "timeframe, naturally offer a reminder — one short line, do not push.\n"
    "Reply EXCLUSIVELY in natural, warm Ukrainian, addressing the user as 'ти'. Write a few short "
    "paragraphs, easy to read — substantial but not a wall. FORMATTING (light — a few per message, "
    "never on every word): wrap a key term or the bottom line in single *asterisks* for bold, and "
    "a gentle caveat in _underscores_ for italic; use '• ' for bullets. No other markup (no "
    "**double**, #, ---, backticks, < >).\n"
    "A deterministic safety check runs alongside you and decides urgency: you are told its level "
    "and must NEVER go below it or imply the user can skip care. NEVER give: a definitive "
    "diagnosis; a medication, supplement, or any dose; calorie/macro/fasting numbers; fabricated "
    "studies, sources, or statistics. Do not use the phrases 'все добре', 'усе добре', "
    "'ти здоровий', 'ти здорова', 'не хвилюйся', 'нічого страшного' — describe the data instead. "
    "Do NOT add your own 'я не лікар' / disclaimer line — it is appended automatically.\n"
    + NATURAL_VOICE
)


@dataclass(frozen=True)
class ConsultReply:
    """One consultation turn's reply (already safety-checked + disclaimer-appended)."""

    text: str
    source: str  # "triage" | "guardrail" | "llm" | "fallback"


def _accumulated_user_text(transcript: list[Turn]) -> str:
    return "\n".join(t["text"] for t in transcript if t.get("role") == "user")


def _safety_lead(decision_source: GateSource, decision: object) -> str | None:
    """The deterministic message that must LEAD the reply, if escalation is warranted — a red-flag
    triage (>= urgent care) or any disordered-eating guardrail signal. The LLM cannot lower it."""
    triage = getattr(decision, "triage", None)
    guardrail = getattr(decision, "guardrail", None)
    if decision_source is GateSource.TRIAGE and triage is not None:
        if triage.action >= Action.URGENT_CARE:
            return str(triage.message)
    elif decision_source is GateSource.GUARDRAIL and guardrail is not None:
        return str(guardrail.message)
    return None


def _prompt(context: str, transcript: list[Turn], *, triage_level: str) -> str:
    lines = [
        "GROUNDED DATA about the subject — answer ONLY from this; do not invent anything:",
        context,
        "",
        f"Deterministic triage level (do not go below this): {triage_level}.",
        "Conversation so far (answer the user's latest message):",
    ]
    for turn in transcript[-MAX_CONTEXT_TURNS:]:
        who = "Користувач" if turn.get("role") == "user" else "Дбайло"
        lines.append(f"{who}: {turn.get('text', '')}")
    return "\n".join(lines)


async def consult(
    context: str,
    transcript: list[Turn],
    *,
    runner: Runner = run_claude,
    model: str | None = None,
) -> ConsultReply:
    """Answer the user's latest question about a grounded subject, safely.

    Runs the deterministic triage backstop over the accumulated user text, then the guarded LLM
    answer grounded in ``context``; a high escalation always leads the reply and is never softened.
    """
    decision = screen(_accumulated_user_text(transcript))
    triage = decision.triage
    triage_level = triage.action.name if triage is not None else Action.MONITOR.name
    lead = _safety_lead(decision.source, decision)

    body = locale.CONSULT_FALLBACK
    source = "fallback"
    model = model or get_settings().claude_chat_model or None  # the (optional) sharper chat model
    try:
        result = await runner(
            _prompt(context, transcript, triage_level=triage_level),
            append_system_prompt=CONSULT_PERSONA,
            model=model,
        )
    except ClaudeUnavailable:
        result = None
    if result is not None and result.ok and result.text.strip():
        # Drop a model-added 'я не лікар' duplicate, KEEP the light *bold*/_italic_ markup (rendered
        # to HTML at send time), and guard the marker-STRIPPED text so a forbidden phrase can't hide
        # behind a marker (e.g. "все *добре*").
        candidate = strip_self_disclaimer(result.text.strip())
        guarded = strip_markup(candidate)
        try:
            assert_safe_output(guarded)
            # Rail #4: the consult may mention clinics, so it must never rank a provider or call one
            # 'best' / 'operate here' / promise a result — reject that like the navigator does.
            if contains_superlative_recommendation(guarded) is not None:
                raise ValueError("superlative provider recommendation")
            body, source = candidate, "llm"
        except ValueError:
            body, source = locale.CONSULT_FALLBACK, "fallback"

    if lead is not None:
        source = decision.source.value
    combined = f"{lead}\n\n{body}" if lead else body
    assert_safe_output(strip_markup(combined))  # belt-and-suspenders; parts are already safe
    return ConsultReply(text=f"{combined}\n\n{DISCLAIMER}", source=source)


# --- Clinic finder (owner-enabled, web-search-backed) ----------------------------
# The owner relaxed rail #4 ("no ranking") for THEIR personal bot: when explicitly asked where to do
# an exam, Дбайло web-searches REAL options (name, address, phone, public rating) in the user's
# city. It is honest about what these are — open-source listings + visitors' opinions, not a
# guarantee — and the OTHER rails still hold (no dose/diagnosis/skip-doctor; a red flag in the query
# still escalates via the gate). This is the ONE place provider ranking is allowed.

CLINIC_FINDER_PERSONA = (
    "You are Дбайло helping the user find WHERE to get a medical exam, analysis, or specialist "
    "visit done, in their city. You CAN use web search — USE IT to return REAL, current options, "
    "never invented ones. From the conversation context you are given, work out exactly what "
    "exam/service/specialist is needed, search for it in the given city, and return up to 5 "
    "concrete options. For each: the name, the address, the phone, and — IMPORTANT — the public "
    "Google rating (e.g. ⭐ 4.4) whenever you can find it; the user specifically values ratings, "
    "so look for them, and omit a rating only if you genuinely cannot find one. Add "
    "a short useful note per option (equipment, online booking, queues, price if public). Prefer "
    "well-known labs/clinics and official sources.\n"
    "Reply EXCLUSIVELY in natural Ukrainian, addressing the user as 'ти'. BE HONEST about what "
    "these are: OPTIONS from open public sources; ratings are visitors' OPINIONS, not a guarantee "
    "of treatment quality or outcome — tell the user to verify the contacts, schedule, and price "
    "themselves, and that the choice is theirs. Briefly add they can check possible НСЗУ free "
    "coverage at " + locale.NSZU_DASHBOARD_URL + ".\n"
    "FORMATTING: a short intro line, then one compact block per option (use '• ' bullets); wrap "
    "each name in *asterisks* for bold. Cite sources as clickable [текст](https://url) links. No "
    "other markup (no **double**, #, ---, backticks, raw < >).\n"
    "NEVER: a definite diagnosis; a medication, supplement, or any dose; a promise that a clinic "
    "will cure them or guarantee a result; telling the user they can skip a doctor. Do NOT add "
    "your own 'я не лікар' / disclaimer line — it is appended automatically.\n" + NATURAL_VOICE
)


async def find_clinics(
    context: str,
    city: str,
    *,
    runner: Runner = run_claude,
    model: str | None = None,
) -> str:
    """Web-search real clinics/labs for the exam discussed in ``context``, in ``city``. Returns the
    formatted Ukrainian text (markup kept for HTML) + disclaimer, or a safe fallback. Gate-screened
    first (a red flag in the text still escalates); guarded (no dose/diagnosis/skip-doctor)."""
    decision = screen(f"{context}\n{city}")
    if decision.short_circuited:
        return decision.message  # a symptom / disordered-eating signal leads, verbatim
    prompt = (
        f"Місто: {city}\n"
        "Контекст консультації (визнач, яке саме обстеження / аналіз / спеціаліст потрібні, "
        f"і шукай саме це):\n{context}"
    )
    try:
        result = await runner(
            prompt,
            append_system_prompt=CLINIC_FINDER_PERSONA,
            allowed_tools=["WebSearch"],
            model=model,
            timeout_s=get_settings().claude_interpret_timeout_s,
        )
    except ClaudeUnavailable:
        result = None
    if result is None or not result.ok or not result.text.strip():
        return f"{assert_safe_output(locale.CONSULT_CLINICS_FALLBACK)}\n\n{DISCLAIMER}"
    body = strip_self_disclaimer(result.text.strip())
    try:
        # Other rails still apply (no dose/diet/skip-doctor); ranking/ratings are intentionally
        # ALLOWED here (the owner relaxed rail #4 for this finder).
        assert_safe_output(strip_markup(body))
    except ValueError:
        return f"{assert_safe_output(locale.CONSULT_CLINICS_FALLBACK)}\n\n{DISCLAIMER}"
    return f"{body}\n\n{DISCLAIMER}"


# --- Reminder extraction (natural-language -> {subject, date}) --------------------
# So the user can just say "нагадай" (or "запиши мене на УЗД 11 липня") and Дбайло fills in WHAT and
# WHEN from the message AND the conversation, instead of always re-asking "про що?". Deterministic
# flow still owns creation/scheduling — this only parses; the subject is safety-checked.

REMINDER_EXTRACT_PERSONA = (
    "You turn a health conversation into a calendar reminder. From the dialogue and the user's "
    "latest request, work out two things: (1) WHAT to remind them to do — an exam, a recheck, a "
    "call to a clinic, a doctor visit — as a SHORT Ukrainian phrase (include the place if it was "
    "named, e.g. 'УЗД нирок та консультація уролога (UROSVIT)'); and (2) the DATE, if one is named "
    "or clearly implied. Output STRICT JSON and nothing else: "
    '{"subject": "<short Ukrainian subject, or empty string if you truly cannot tell what to '
    'remind about>", "date": "<YYYY-MM-DD, or empty string if no date is given>"}. '
    "Resolve relative and Ukrainian dates against today's date (given below); for a day+month "
    "with no year, choose the NEXT future occurrence. NEVER put a medication dose in the subject. "
    "If the request is vague and the conversation gives no clue, return an empty subject."
)


@dataclass(frozen=True)
class ReminderDraft:
    """A parsed reminder: what to remind about + an optional ISO date ("" when none)."""

    subject: str
    date: str


def _extract_json(text: str) -> dict[str, object] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def extract_reminder(
    latest_text: str,
    transcript: list[Turn],
    *,
    today: date,
    runner: Runner = run_claude,
    model: str | None = None,
) -> ReminderDraft | None:
    """Infer a reminder's subject + date from the user's request and the recent conversation, or
    ``None`` when the subject can't be told (the caller then asks 'про що?'). Pure parsing — the
    creation/scheduling stay deterministic; the subject passes ``assert_safe_output`` (no dose)."""
    convo = "\n".join(
        f"{'Користувач' if t.get('role') == 'user' else 'Дбайло'}: {t.get('text', '')}"
        for t in transcript[-MAX_CONTEXT_TURNS:]
    )
    ask = latest_text.strip() or "(користувач натиснув кнопку «Нагадати»)"
    prompt = (
        f"Сьогодні: {today.isoformat()}.\n"
        f"Розмова:\n{convo}\n\n"
        f"Останнє прохання користувача про нагадування: {ask}\n"
        "Витягни предмет і дату нагадування у вказаному форматі JSON."
    )
    try:
        result = await runner(prompt, append_system_prompt=REMINDER_EXTRACT_PERSONA, model=model)
    except ClaudeUnavailable:
        return None
    if result is None or not result.ok or not result.text.strip():
        return None
    data = _extract_json(result.text)
    if data is None:
        return None
    subject = str(data.get("subject") or "").strip()
    date_str = str(data.get("date") or "").strip()
    if not subject:
        return None
    try:
        assert_safe_output(subject)  # a reminder subject must carry no dose / forbidden phrasing
    except ValueError:
        return None
    return ReminderDraft(subject=subject, date=date_str)
