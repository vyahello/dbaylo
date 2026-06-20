"""Humanization tests: safety guard + deterministic fallback + disclaimer."""

from __future__ import annotations

from datetime import date

from dbaylo.labs.humanize import deterministic_summary, humanize, interpret
from dbaylo.labs.trends import LabPoint, compute_trend
from dbaylo.llm import ClaudeResult, ClaudeUnavailable
from dbaylo.triage.safety import DISCLAIMER, contains_dose_directive, contains_forbidden_reassurance


def _summaries():
    glucose = compute_trend(
        [
            LabPoint("Глюкоза", date(2026, 1, 1), 7.0, "ммоль/л", 3.9, 6.1),
            LabPoint("Глюкоза", date(2026, 2, 1), 5.4, "ммоль/л", 3.9, 6.1),
        ]
    )
    hb = compute_trend(
        [
            LabPoint("Гемоглобін", date(2026, 1, 1), 140.0, "г/л", 130.0, 160.0),
            LabPoint("Гемоглобін", date(2026, 2, 1), 145.0, "г/л", 130.0, 160.0),
        ]
    )
    return [glucose, hb]


def _runner(text: str, ok: bool = True):
    async def run(*args, **kwargs) -> ClaudeResult:
        return ClaudeResult(ok=ok, text=text, raw_stdout=text, exit_code=0 if ok else 1)

    return run


def test_deterministic_summary_is_safe_and_mentions_values() -> None:
    text = deterministic_summary(_summaries())
    assert contains_forbidden_reassurance(text) is None
    assert contains_dose_directive(text) is None  # "140 г/л" must NOT trip the guard
    assert "Глюкоза" in text and "Гемоглобін" in text


async def test_humanize_uses_safe_model_text() -> None:
    body = "Твоя глюкоза повернулася в межі норми. Варто показати результати лікарю."
    out = await humanize(_summaries(), runner=_runner(body))
    assert body in out
    assert out.endswith(DISCLAIMER)


async def test_humanize_falls_back_on_unsafe_model_text() -> None:
    unsafe = "Все добре, можеш не йти до лікаря. Приймай 2 таблетки на день."
    out = await humanize(_summaries(), runner=_runner(unsafe))
    assert unsafe not in out
    assert contains_forbidden_reassurance(out) is None
    assert contains_dose_directive(out) is None
    assert out.endswith(DISCLAIMER)


async def test_humanize_falls_back_when_call_not_ok() -> None:
    out = await humanize(_summaries(), runner=_runner("", ok=False))
    assert "Ось що я бачу" in out
    assert out.endswith(DISCLAIMER)


async def test_humanize_falls_back_when_claude_unavailable() -> None:
    async def boom(*args, **kwargs):
        raise ClaudeUnavailable("no binary")

    out = await humanize(_summaries(), runner=boom)
    assert out.endswith(DISCLAIMER)


async def test_humanize_empty_summaries_still_safe() -> None:
    out = await humanize([], runner=_runner("anything"))
    assert out.endswith(DISCLAIMER)


# --- Stage 5: expert interpretation ---------------------------------------------


def _report(*, conclusion=None, flagged=False):
    from dbaylo.labs.schema import ExtractedAnalyte, ExtractedReport

    return ExtractedReport(
        lab="Synevo",
        conclusion=conclusion,
        results=[
            ExtractedAnalyte(
                "Глюкоза",
                value=7.0,
                unit="ммоль/л",
                ref_low=3.9,
                ref_high=6.1,
                out_of_range=flagged,
            ),
            ExtractedAnalyte(
                "Колір", value=None, value_text="жовтий", ref_text="жовтий", out_of_range=False
            ),
        ],
    )


def test_deterministic_interpretation_all_normal_is_safe() -> None:
    from dbaylo.labs.humanize import deterministic_interpretation
    from dbaylo.locale import LAB_INTERPRET_ALL_NORMAL

    text = deterministic_interpretation(_report(conclusion="Нормозооспермія", flagged=False))
    assert "Нормозооспермія" in text and LAB_INTERPRET_ALL_NORMAL in text
    assert contains_forbidden_reassurance(text) is None  # data terms, not "все добре"


def test_deterministic_interpretation_lists_flagged() -> None:
    from dbaylo.labs.humanize import deterministic_interpretation

    text = deterministic_interpretation(_report(flagged=True))
    assert "Глюкоза" in text  # the out-of-range row is surfaced
    assert "Колір" not in text  # the ok row is not


async def test_interpret_uses_safe_model_text() -> None:
    body = "Загалом показники в межах норми. Варто обговорити з лікарем за потреби."
    out = await interpret(_report(conclusion="Нормозооспермія"), _summaries(), runner=_runner(body))
    assert body in out and out.endswith(DISCLAIMER)


async def test_interpret_falls_back_on_forbidden_phrase() -> None:
    # If the model says "все добре" (a forbidden reassurance), we must not send it.
    out = await interpret(_report(), _summaries(), runner=_runner("Все добре, не хвилюйся!"))
    assert "не хвилюйся" not in out
    assert contains_forbidden_reassurance(out) is None


async def test_interpret_guard_sees_through_markup() -> None:
    # A forbidden phrase must not slip past by hiding a *bold* marker inside it.
    out = await interpret(_report(), _summaries(), runner=_runner("Усе *добре*, не хвилюйся!"))
    assert "добре" not in out  # tripped the guard despite the marker -> deterministic fallback
    assert contains_forbidden_reassurance(out) is None


async def test_interpret_retries_once_on_a_transient_failure() -> None:
    calls: list[int] = []
    good = "Загалом показники в межах норми. Варто обговорити з лікарем за потреби."

    async def flaky(*args, **kwargs) -> ClaudeResult:
        calls.append(1)
        if len(calls) == 1:  # first call a transient failure (e.g. API overload)
            return ClaudeResult(ok=False, text="", raw_stdout="", exit_code=1, error="overloaded")
        return ClaudeResult(ok=True, text=good, raw_stdout=good, exit_code=0)

    out = await interpret(_report(), _summaries(), runner=flaky)
    assert good in out and len(calls) == 2  # retried, then delivered the real reading


async def test_interpret_does_not_retry_a_timeout() -> None:
    calls: list[int] = []

    async def timed_out(*args, **kwargs) -> ClaudeResult:
        calls.append(1)
        return ClaudeResult(ok=False, text="", raw_stdout="", exit_code=None, error="timeout")

    out = await interpret(_report(conclusion="Нормозооспермія"), _summaries(), runner=timed_out)
    assert "Нормозооспермія" in out  # deterministic fallback
    assert len(calls) == 1  # a real timeout is NOT retried (it would just time out again)


async def test_interpret_falls_back_when_claude_unavailable() -> None:
    async def boom(*args, **kwargs):
        raise ClaudeUnavailable("no binary")

    out = await interpret(_report(conclusion="Нормозооспермія"), _summaries(), runner=boom)
    assert "Нормозооспермія" in out and out.endswith(DISCLAIMER)


# --- Stage 6: narrative document interpretation ---------------------------------


def _narrative():
    from dbaylo.labs.schema import ExtractedReport

    return ExtractedReport(
        report_type="МРТ головного мозку",
        narrative="Без вогнищевих змін інтенсивності сигналу.",
        conclusion="МРТ ознак патологічних змін не виявлено.",
    )


def test_deterministic_interpretation_narrative() -> None:
    from dbaylo.labs.humanize import deterministic_interpretation

    text = deterministic_interpretation(_narrative())
    assert "МРТ головного мозку" in text
    assert "не виявлено" in text
    assert contains_forbidden_reassurance(text) is None


async def test_interpret_narrative_uses_model_text() -> None:
    body = "За описом МРТ — у межах норми. За потреби обговори результат з неврологом."
    out = await interpret(_narrative(), [], runner=_runner(body))
    assert body in out and out.endswith(DISCLAIMER)
