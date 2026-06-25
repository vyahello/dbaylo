"""Contextual consultation engine (``companion.consult``) — grounded answer + safety backstop.

The consult gives an LLM answer grounded in a deterministic context, but the deterministic triage
core still owns escalation: a red flag LEADS the reply and is never softened; every reply is guarded
and disclaimer-appended; any LLM failure / guard trip falls back to a safe template. The model must
also be HANDED the grounded context (so it answers from real data, not invention).
"""

from __future__ import annotations

from datetime import date

from dbaylo import locale
from dbaylo.companion import consult
from dbaylo.llm import ClaudeResult, ClaudeUnavailable
from dbaylo.safety import screen
from dbaylo.triage.safety import DISCLAIMER, contains_dose_directive, contains_forbidden_reassurance

_CONTEXT = (
    "Subject: a single lab indicator — 'Холестерин' (sample: blood).\n"
    "Measurements over time (date | value | reference | status):\n"
    "- 2023-01-01 | 6.2 ммоль/л | ≤ 5.2 | OUT OF RANGE\n"
    "Range-relative trend across these points: STABLE_OUT_OF_RANGE."
)


def _runner(text: str, ok: bool = True):
    captured: dict[str, object] = {}

    async def run(prompt: str, *args, **kwargs) -> ClaudeResult:
        captured["prompt"] = prompt
        captured.update(kwargs)  # capture allowed_tools / append_system_prompt / model / ...
        return ClaudeResult(ok=ok, text=text, raw_stdout=text, exit_code=0 if ok else 1)

    run.captured = captured  # type: ignore[attr-defined]
    return run


async def test_consult_grounds_the_answer_and_appends_disclaimer() -> None:
    body = "Твій холестерин 6.2 вищий за норму ≤5.2 — варто обговорити це з лікарем."
    transcript = [{"role": "user", "text": "що означає мій холестерин?"}]
    runner = _runner(body)
    reply = await consult.consult(_CONTEXT, transcript, runner=runner)
    assert body in reply.text and reply.text.endswith(DISCLAIMER)
    assert reply.source == "llm"
    # The grounded context AND the user's question were handed to the model.
    assert "Холестерин" in runner.captured["prompt"]  # type: ignore[attr-defined]
    assert "що означає мій холестерин" in runner.captured["prompt"]  # type: ignore[attr-defined]


async def test_consult_falls_back_on_forbidden_phrase() -> None:
    transcript = [{"role": "user", "text": "це погано?"}]
    reply = await consult.consult(
        _CONTEXT, transcript, runner=_runner("Все добре, до лікаря йти не треба.")
    )
    assert "не треба" not in reply.text
    assert contains_forbidden_reassurance(reply.text) is None
    assert locale.CONSULT_FALLBACK in reply.text and reply.source == "fallback"


async def test_consult_falls_back_when_claude_unavailable() -> None:
    async def boom(*args, **kwargs):
        raise ClaudeUnavailable("no binary")

    reply = await consult.consult(_CONTEXT, [{"role": "user", "text": "?"}], runner=boom)
    assert locale.CONSULT_FALLBACK in reply.text and reply.text.endswith(DISCLAIMER)


async def test_consult_triage_backstop_leads_on_emergency() -> None:
    # A red flag in the user's question must LEAD the reply (deterministic), even mid-consult about
    # a lab value — the LLM can never lower it.
    transcript = [{"role": "user", "text": "а ще я не можу помочитися"}]
    reply = await consult.consult(
        _CONTEXT, transcript, runner=_runner("Поговорімо про холестерин.")
    )
    escalation = screen("а ще я не можу помочитися").triage
    assert escalation is not None
    assert escalation.message in reply.text  # the deterministic escalation leads
    assert reply.source == "triage"


async def test_consult_preserves_light_markup_for_premium_rendering() -> None:
    # The reply KEEPS the *bold*/_italic_ markers (the flow renders them to HTML) — it no longer
    # strips them to plain text.
    body = "Твій *холестерин 6.2* трохи вищий. _Не гостро, але варто перевірити._"
    reply = await consult.consult(_CONTEXT, [{"role": "user", "text": "?"}], runner=_runner(body))
    assert "*холестерин 6.2*" in reply.text and "_Не гостро" in reply.text


async def test_consult_strips_a_model_added_duplicate_disclaimer() -> None:
    # The model sometimes appends its OWN 'я не лікар' line; we always append the canonical
    # DISCLAIMER, so the duplicate is dropped — exactly one disclaimer remains.
    body = "Холестерин трохи вищий.\n\nЯ не лікар, і це не замінює консультацію з фахівцем."
    reply = await consult.consult(_CONTEXT, [{"role": "user", "text": "?"}], runner=_runner(body))
    assert "не замінює консультацію з фахівцем" not in reply.text  # the model's duplicate is gone
    assert reply.text.endswith(DISCLAIMER)  # the one canonical disclaimer remains


async def test_consult_rejects_a_superlative_clinic_recommendation() -> None:
    # The consult may discuss clinics now, so rail #4 applies: ranking a provider as "best" /
    # "operate here" must trip the guard -> deterministic fallback, never sent.
    body = "Найкраща клініка для цього — «Оберіг», оперуйся саме там."
    reply = await consult.consult(
        _CONTEXT, [{"role": "user", "text": "де зробити?"}], runner=_runner(body)
    )
    assert "Найкраща клініка" not in reply.text and reply.source == "fallback"


async def test_find_clinics_returns_web_results_and_allows_ratings() -> None:
    # The owner-enabled finder uses web search and MAY include ratings (rail #4 relaxed here) — but
    # the other rails hold and the disclaimer is appended. The web tool must be requested.
    body = "Ось варіанти:\n• *ДІЛА* — вул. Тестова 1, ☎ 000, ⭐ 4.5 Google\nПеревір контакти."
    runner = _runner(body)
    out = await consult.find_clinics("аналіз сечі", "Львів", runner=runner)
    assert "ДІЛА" in out and "4.5" in out  # ratings kept (no superlative guard here)
    assert out.endswith(DISCLAIMER)
    assert runner.captured.get("allowed_tools") == ["WebSearch"]  # type: ignore[attr-defined]


async def test_find_clinics_still_escalates_a_red_flag() -> None:
    # The gate runs first: a red flag in the text must lead, not a clinic list.
    out = await consult.find_clinics("не можу помочитися", "Львів", runner=_runner("список клінік"))
    escalation = screen("не можу помочитися").triage
    assert escalation is not None and escalation.message in out


async def test_find_clinics_falls_back_when_unavailable() -> None:
    out = await consult.find_clinics("аналіз сечі", "Львів", runner=_runner("", ok=False))
    assert locale.CONSULT_CLINICS_FALLBACK in out and out.endswith(DISCLAIMER)


def test_consult_fallback_and_persona_are_safe() -> None:
    assert contains_forbidden_reassurance(locale.CONSULT_FALLBACK) is None
    assert contains_dose_directive(locale.CONSULT_FALLBACK) is None
    assert contains_forbidden_reassurance(locale.CONSULT_CLINICS_FALLBACK) is None


async def test_extract_reminder_parses_subject_and_date_from_context() -> None:
    # The user's request gives the subject; the model resolves the date — so the flow can create the
    # reminder without re-asking. The conversation is handed to the model for context.
    runner = _runner(
        '{"subject": "УЗД нирок та консультація уролога (UROSVIT)", "date": "2026-07-11"}'
    )
    transcript = [{"role": "user", "text": "обговорюємо УЗД нирок і уролога"}]
    draft = await consult.extract_reminder(
        "зроби нагадування", transcript, today=date(2026, 6, 25), runner=runner
    )
    assert draft is not None
    assert draft.subject == "УЗД нирок та консультація уролога (UROSVIT)"
    assert draft.date == "2026-07-11"
    assert "2026-06-25" in runner.captured["prompt"]  # type: ignore[attr-defined]
    assert "обговорюємо УЗД" in runner.captured["prompt"]  # type: ignore[attr-defined]


async def test_extract_reminder_empty_subject_means_ask() -> None:
    # No inferable subject -> None, so the flow falls back to asking "про що?".
    draft = await consult.extract_reminder(
        "нагадай", [], today=date(2026, 6, 25), runner=_runner('{"subject": "", "date": ""}')
    )
    assert draft is None


async def test_extract_reminder_no_date_is_kept_empty() -> None:
    draft = await consult.extract_reminder(
        "нагадай здати аналіз сечі",
        [],
        today=date(2026, 6, 25),
        runner=_runner('{"subject": "Здати аналіз сечі", "date": ""}'),
    )
    assert draft is not None and draft.subject == "Здати аналіз сечі" and draft.date == ""


async def test_extract_reminder_rejects_an_unsafe_subject() -> None:
    # A subject that smuggles a dose directive must be rejected (no dose in a reminder, rail #1).
    draft = await consult.extract_reminder(
        "нагадай",
        [],
        today=date(2026, 6, 25),
        runner=_runner('{"subject": "приймай 2 таблетки на день", "date": "2026-07-01"}'),
    )
    assert draft is None


async def test_extract_reminder_tolerates_garbage_output() -> None:
    assert (
        await consult.extract_reminder(
            "нагадай", [], today=date(2026, 6, 25), runner=_runner("не json взагалі")
        )
        is None
    )
    assert (
        await consult.extract_reminder(
            "нагадай", [], today=date(2026, 6, 25), runner=_runner("", ok=False)
        )
        is None
    )
