"""Callback-data tokens shared between the proactive senders (companion) and the bot
callback handlers (bot/). Kept aiogram-free so companion never imports the bot layer.
"""

from __future__ import annotations

_SEP = ":"

PROBLEM_RESOLVE = "prob_resolve"
PROBLEM_RENAME = "prob_rename"
REMINDER_OFF = "rem_off"
MEDICATION_OFF = "med_off"

# Tier 1.2 — history & retrieval. All carry only ids/indices (well within the 64-byte
# callback-data limit); analyte names are looked up by index, never embedded.
HIST_FILE = "hist_file"
HIST_RESULTS = "hist_results"
HIST_DELETE = "hist_del"
HIST_DELETE_OK = "hist_delok"
HIST_DELETE_NO = "hist_delno"
HIST_TREND = "hist_trend"
HIST_CLEAN = "hist_clean"
HIST_INTERPRET = "hist_interp"  # show (or generate) the expert reading for a confirmed report
# Master-detail navigation + focused views (the UX redesign).
HIST_PAGE = "hist_page"  # paginate the report list (edit-in-place)
HIST_OPEN = "hist_open"  # open a report's card (carries the page to return to)
HIST_BACK = "hist_back"  # back to the list at a given page
HIST_RESULTS_ALL = "hist_resall"  # the FULL results table (opt-in from the problems view)
HIST_DYNAMICS = "hist_dyn"  # trend charts for the flagged analytes only
HIST_INTERP_REFRESH = "hist_iref"  # regenerate a cached analysis
HIST_INTERP_DEL = "hist_idel"  # delete a saved analysis


def _make(prefix: str, ident: int) -> str:
    return f"{prefix}{_SEP}{ident}"


def _parse(prefix: str, data: str) -> int | None:
    head, _, rest = data.partition(_SEP)
    return int(rest) if head == prefix and rest.isdigit() else None


def problem_resolve(condition_id: int) -> str:
    return _make(PROBLEM_RESOLVE, condition_id)


def parse_problem_resolve(data: str) -> int | None:
    return _parse(PROBLEM_RESOLVE, data)


def problem_rename(condition_id: int) -> str:
    return _make(PROBLEM_RENAME, condition_id)


def parse_problem_rename(data: str) -> int | None:
    return _parse(PROBLEM_RENAME, data)


def reminder_off(reminder_id: int) -> str:
    return _make(REMINDER_OFF, reminder_id)


def parse_reminder_off(data: str) -> int | None:
    return _parse(REMINDER_OFF, data)


def medication_off(medication_id: int) -> str:
    return _make(MEDICATION_OFF, medication_id)


def parse_medication_off(data: str) -> int | None:
    return _parse(MEDICATION_OFF, data)


# --- Tier 1.2: history & retrieval ----------------------------------------------


def history_file(report_id: int) -> str:
    return _make(HIST_FILE, report_id)


def parse_history_file(data: str) -> int | None:
    return _parse(HIST_FILE, data)


def history_results(report_id: int) -> str:
    return _make(HIST_RESULTS, report_id)


def parse_history_results(data: str) -> int | None:
    return _parse(HIST_RESULTS, data)


def history_delete(report_id: int) -> str:
    return _make(HIST_DELETE, report_id)


def parse_history_delete(data: str) -> int | None:
    return _parse(HIST_DELETE, data)


def history_delete_ok(report_id: int) -> str:
    return _make(HIST_DELETE_OK, report_id)


def parse_history_delete_ok(data: str) -> int | None:
    return _parse(HIST_DELETE_OK, data)


def history_delete_no(report_id: int) -> str:
    return _make(HIST_DELETE_NO, report_id)


def parse_history_delete_no(data: str) -> int | None:
    return _parse(HIST_DELETE_NO, data)


def history_interpret(report_id: int) -> str:
    return _make(HIST_INTERPRET, report_id)


def parse_history_interpret(data: str) -> int | None:
    return _parse(HIST_INTERPRET, data)


def history_results_all(report_id: int) -> str:
    return _make(HIST_RESULTS_ALL, report_id)


def parse_history_results_all(data: str) -> int | None:
    return _parse(HIST_RESULTS_ALL, data)


def history_dynamics(report_id: int) -> str:
    return _make(HIST_DYNAMICS, report_id)


def parse_history_dynamics(data: str) -> int | None:
    return _parse(HIST_DYNAMICS, data)


def history_interpret_refresh(report_id: int) -> str:
    return _make(HIST_INTERP_REFRESH, report_id)


def parse_history_interpret_refresh(data: str) -> int | None:
    return _parse(HIST_INTERP_REFRESH, data)


def history_interpret_del(report_id: int) -> str:
    return _make(HIST_INTERP_DEL, report_id)


def parse_history_interpret_del(data: str) -> int | None:
    return _parse(HIST_INTERP_DEL, data)


def history_page(page: int) -> str:
    return _make(HIST_PAGE, page)


def parse_history_page(data: str) -> int | None:
    return _parse(HIST_PAGE, data)


def history_back(page: int) -> str:
    return _make(HIST_BACK, page)


def parse_history_back(data: str) -> int | None:
    return _parse(HIST_BACK, data)


def history_open(report_id: int, page: int) -> str:
    return f"{HIST_OPEN}{_SEP}{report_id}{_SEP}{page}"


def parse_history_open(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, page = rest.partition(_SEP)
    if head == HIST_OPEN and rid.isdigit() and page.isdigit():
        return int(rid), int(page)
    return None


def history_trend(report_id: int, index: int) -> str:
    return f"{HIST_TREND}{_SEP}{report_id}{_SEP}{index}"


def parse_history_trend(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, idx = rest.partition(_SEP)
    if head == HIST_TREND and rid.isdigit() and idx.isdigit():
        return int(rid), int(idx)
    return None


# --- Tier 1.3: button-menu section actions (static, no ids) ----------------------

MENU_OPEN_HISTORY = "menu_hist"
MENU_GOALS_LIST = "menu_goals"
MENU_GOAL_NEW = "menu_goal_new"
MENU_PROB_LIST = "menu_probs"
MENU_PROB_NEW = "menu_prob_new"
MENU_MED_LIST = "menu_meds"
MENU_MED_NEW = "menu_med_new"
MENU_PRICE = "menu_price"
MENU_COVERAGE = "menu_coverage"
# The one shared dialog-cancel callback (handled centrally; clears any active FSM).
CANCEL_DIALOG = "menu_cancel"
