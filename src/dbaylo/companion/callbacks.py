"""Callback-data tokens shared between the proactive senders (companion) and the bot
callback handlers (bot/). Kept aiogram-free so companion never imports the bot layer.
"""

from __future__ import annotations

_SEP = ":"

PROBLEM_RESOLVE = "prob_resolve"
PROBLEM_RENAME = "prob_rename"
# AI-proposed problems (category master-detail). Track / wave off a finding by (category, INDEX in
# the freshly-derived flat proposal list) — the category is only so the SAME detail re-renders after
# the tap; the finding itself is re-resolved by index, like the charts picker. category "watch" is
# the on-the-edge detail.
PROBLEM_TRACK = "prob_track"
PROBLEM_DISMISS = "prob_dismiss"
PROBLEM_CAT = "prob_cat"  # open one category's out-of-range detail (carries the category key)
PROBLEM_BACK = "prob_pback"  # back to the grouped top level (static, edit-in-place)
PROBLEM_TRACKED = "prob_trkd"  # open the "під наглядом" detail (static)
PROBLEM_DISMISSED = "prob_dmd"  # open the "відкладені" restore detail (static)
PROBLEM_RESTORE = "prob_rest"  # restore one dismissed finding (carries its condition_id)
PROBLEM_RESOLVED_LIST = "prob_rslvd"  # open the "✔️ Вирішені" archive (static)
PROBLEM_REOPEN = "prob_reopen"  # re-open one resolved concern → ACTIVE (carries its condition_id)
# AI-suggested goals: adopt one by its INDEX in the freshly-derived suggestion list (computed, not a
# DB row; re-resolved on tap like the problems proposals). Adopted goals are then manageable rows
# addressed by their goal_id: ✅ achieved / 🗑 removed (undo an accidental adopt).
GOAL_ADOPT = "goal_adopt"
GOAL_ACHIEVE = "goal_done"  # mark an active goal achieved (carries the goal_id)
GOAL_REMOVE = "goal_rm"  # drop an active goal (carries the goal_id)
# Goals are a master-detail: the master lists short subjects, a tap opens the detail (full title +
# the indicator's problem history) where the action lives.
GOAL_VIEW_SUG = "goal_vsug"  # open a SUGGESTION's detail by its index in the proposal list
GOAL_VIEW = "goal_view"  # open an adopted goal's detail by its goal_id
GOAL_BACK = "goal_gback"  # back to the goals master (static, edit-in-place)
GOAL_ARCHIVE = "goal_arch"  # open the «🗄 Закриті цілі» archive (static)
GOAL_REOPEN = "goal_reop"  # restore a closed goal → ACTIVE (carries the goal_id)
REMINDER_OFF = "rem_off"
MEDICATION_OFF = "med_off"
# Reminders master-detail: a list tap OPENS the item (read) instead of deleting it; turning it
# off is a deliberate button inside the card.
REMINDER_VIEW = "rem_view"
MEDICATION_VIEW = "med_view"
REMINDERS_BACK = "rem_back"  # back to the reminders list (static, edit-in-place)
MED_LIST_BACK = "med_lback"  # back to the medications list (static, edit-in-place)
# A medication card is reachable from the 💊 meds list AND the 🔔 reminders list; medication_view /
# medication_off carry an ORIGIN ('m' meds list, 'r' reminders list) so «Назад» / a turn-off return
# the user to the list they came from.

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
HIST_INTERP_VIEW = "hist_iview"  # open one section of the analysis (drill-down: 0=overview..3)


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


def _make_cat(prefix: str, category: str, index: int) -> str:
    return f"{prefix}{_SEP}{category}{_SEP}{index}"


def _parse_cat(prefix: str, data: str) -> tuple[str, int] | None:
    head, _, rest = data.partition(_SEP)
    category, _, idx = rest.partition(_SEP)
    return (category, int(idx)) if head == prefix and category and idx.isdigit() else None


def problem_track(category: str, index: int) -> str:
    return _make_cat(PROBLEM_TRACK, category, index)


def parse_problem_track(data: str) -> tuple[str, int] | None:
    return _parse_cat(PROBLEM_TRACK, data)


def problem_dismiss(category: str, index: int) -> str:
    return _make_cat(PROBLEM_DISMISS, category, index)


def parse_problem_dismiss(data: str) -> tuple[str, int] | None:
    return _parse_cat(PROBLEM_DISMISS, data)


def problem_category(category: str) -> str:
    return f"{PROBLEM_CAT}{_SEP}{category}"


def parse_problem_category(data: str) -> str | None:
    head, _, rest = data.partition(_SEP)
    return rest if head == PROBLEM_CAT and rest else None


def problem_restore(condition_id: int) -> str:
    return _make(PROBLEM_RESTORE, condition_id)


def parse_problem_restore(data: str) -> int | None:
    return _parse(PROBLEM_RESTORE, data)


def problem_reopen(condition_id: int) -> str:
    return _make(PROBLEM_REOPEN, condition_id)


def parse_problem_reopen(data: str) -> int | None:
    return _parse(PROBLEM_REOPEN, data)


def goal_adopt(index: int) -> str:
    return _make(GOAL_ADOPT, index)


def parse_goal_adopt(data: str) -> int | None:
    return _parse(GOAL_ADOPT, data)


def goal_achieve(goal_id: int) -> str:
    return _make(GOAL_ACHIEVE, goal_id)


def parse_goal_achieve(data: str) -> int | None:
    return _parse(GOAL_ACHIEVE, data)


def goal_remove(goal_id: int) -> str:
    return _make(GOAL_REMOVE, goal_id)


def parse_goal_remove(data: str) -> int | None:
    return _parse(GOAL_REMOVE, data)


def goal_reopen(goal_id: int) -> str:
    return _make(GOAL_REOPEN, goal_id)


def parse_goal_reopen(data: str) -> int | None:
    return _parse(GOAL_REOPEN, data)


def goal_view_sug(index: int) -> str:
    return _make(GOAL_VIEW_SUG, index)


def parse_goal_view_sug(data: str) -> int | None:
    return _parse(GOAL_VIEW_SUG, data)


def goal_view(goal_id: int) -> str:
    return _make(GOAL_VIEW, goal_id)


def parse_goal_view(data: str) -> int | None:
    return _parse(GOAL_VIEW, data)


def reminder_off(reminder_id: int) -> str:
    return _make(REMINDER_OFF, reminder_id)


def parse_reminder_off(data: str) -> int | None:
    return _parse(REMINDER_OFF, data)


def _make_origin(prefix: str, ident: int, origin: str) -> str:
    return f"{prefix}{_SEP}{ident}{_SEP}{origin}"


def _parse_origin(prefix: str, data: str) -> tuple[int, str] | None:
    head, _, rest = data.partition(_SEP)
    ident, _, origin = rest.partition(_SEP)
    return (int(ident), origin) if head == prefix and ident.isdigit() and origin else None


def medication_off(medication_id: int, origin: str = "r") -> str:
    return _make_origin(MEDICATION_OFF, medication_id, origin)


def parse_medication_off(data: str) -> tuple[int, str] | None:
    return _parse_origin(MEDICATION_OFF, data)


def reminder_view(reminder_id: int) -> str:
    return _make(REMINDER_VIEW, reminder_id)


def parse_reminder_view(data: str) -> int | None:
    return _parse(REMINDER_VIEW, data)


def medication_view(medication_id: int, origin: str = "r") -> str:
    return _make_origin(MEDICATION_VIEW, medication_id, origin)


def parse_medication_view(data: str) -> tuple[int, str] | None:
    return _parse_origin(MEDICATION_VIEW, data)


# Open the original prescription photo/PDF a medication was read from (from its card).
MEDICATION_FILE = "med_file"


def medication_file(medication_id: int, origin: str = "m") -> str:
    return _make_origin(MEDICATION_FILE, medication_id, origin)


def parse_medication_file(data: str) -> tuple[int, str] | None:
    return _parse_origin(MEDICATION_FILE, data)


# A PRESCRIPTION / course group (its meds fire separately, but list + photo are one). Addressed by a
# REPRESENTATIVE medication id (any med in the course) — its ``course`` label gathers the rest.
COURSE_VIEW = "course_view"
COURSE_FILE = "course_file"
COURSE_OFF = "course_off"


def course_view(medication_id: int, origin: str = "m") -> str:
    return _make_origin(COURSE_VIEW, medication_id, origin)


def parse_course_view(data: str) -> tuple[int, str] | None:
    return _parse_origin(COURSE_VIEW, data)


def course_file(medication_id: int, origin: str = "m") -> str:
    return _make_origin(COURSE_FILE, medication_id, origin)


def parse_course_file(data: str) -> tuple[int, str] | None:
    return _parse_origin(COURSE_FILE, data)


def course_off(medication_id: int, origin: str = "m") -> str:
    return _make_origin(COURSE_OFF, medication_id, origin)


def parse_course_off(data: str) -> tuple[int, str] | None:
    return _parse_origin(COURSE_OFF, data)


# Archive of FINISHED prescriptions (all meds turned off, or the course's term has passed). The
# record + photo are kept; the user can re-open or restore them.
MED_ARCHIVE = "med_archive"  # open the archive list (no id)
COURSE_ARCHIVED = "course_arch"  # view an archived course card
COURSE_RESTORE = "course_rest"  # re-activate an archived course's reminders


def course_archived(medication_id: int, origin: str = "m") -> str:
    return _make_origin(COURSE_ARCHIVED, medication_id, origin)


def parse_course_archived(data: str) -> tuple[int, str] | None:
    return _parse_origin(COURSE_ARCHIVED, data)


def course_restore(medication_id: int, origin: str = "m") -> str:
    return _make_origin(COURSE_RESTORE, medication_id, origin)


def parse_course_restore(data: str) -> tuple[int, str] | None:
    return _parse_origin(COURSE_RESTORE, data)


# PERMANENTLY delete a whole prescription (meds + reminders + photo) — two-step, with a confirm.
COURSE_DELETE = "course_del"  # ask to confirm
COURSE_DELETE_YES = "course_del_y"  # do it


def course_delete(medication_id: int, origin: str = "m") -> str:
    return _make_origin(COURSE_DELETE, medication_id, origin)


def parse_course_delete(data: str) -> tuple[int, str] | None:
    return _parse_origin(COURSE_DELETE, data)


def course_delete_yes(medication_id: int, origin: str = "m") -> str:
    return _make_origin(COURSE_DELETE_YES, medication_id, origin)


def parse_course_delete_yes(data: str) -> tuple[int, str] | None:
    return _parse_origin(COURSE_DELETE_YES, data)


# Hard-delete a medication's reminders FROM ITS REMINDER CARD (distinct from the /medication list's
# soft turn-off, MEDICATION_OFF) — there's no re-enable, so the card "removes" them for real.
MEDICATION_DELETE = "med_del"


def medication_delete(medication_id: int) -> str:
    return _make(MEDICATION_DELETE, medication_id)


def parse_medication_delete(data: str) -> int | None:
    return _parse(MEDICATION_DELETE, data)


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


def history_interpret_view(report_id: int, index: int) -> str:
    return f"{HIST_INTERP_VIEW}{_SEP}{report_id}{_SEP}{index}"


def parse_history_interpret_view(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, idx = rest.partition(_SEP)
    if head == HIST_INTERP_VIEW and rid.isdigit() and idx.isdigit():
        return int(rid), int(idx)
    return None


# Charts picker (a paginated list of one button per trending analyte → its single chart),
# instead of dumping every chart at once. Shared by the post-confirm chain and /history.
CHART_OPEN = "chart_open"  # open the picker for a report (page 0)
CHART_PAGE = "chart_page"  # paginate the picker
CHART_PICK = "chart_pick"  # render ONE analyte's chart (by index into the trend list)
CHART_NAV = "chart_nav"  # flip to the prev/next analyte's chart IN PLACE (carousel)
CHART_ALL = "chart_all"  # opt-in: a single text report of all trends
CHART_PDF = "chart_pdf"  # opt-in: one PDF with every chart + a short description


def chart_open(report_id: int) -> str:
    return _make(CHART_OPEN, report_id)


def parse_chart_open(data: str) -> int | None:
    return _parse(CHART_OPEN, data)


def chart_all(report_id: int) -> str:
    return _make(CHART_ALL, report_id)


def parse_chart_all(data: str) -> int | None:
    return _parse(CHART_ALL, data)


def chart_pdf(report_id: int) -> str:
    return _make(CHART_PDF, report_id)


def parse_chart_pdf(data: str) -> int | None:
    return _parse(CHART_PDF, data)


def chart_page(report_id: int, page: int) -> str:
    return f"{CHART_PAGE}{_SEP}{report_id}{_SEP}{page}"


def parse_chart_page(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, page = rest.partition(_SEP)
    if head == CHART_PAGE and rid.isdigit() and page.isdigit():
        return int(rid), int(page)
    return None


# Dynamics browser: indicators grouped by clinical category, across all labs.
DYN_OPEN = "dyn_open"  # open the browser as a NEW message (from the /history list)
DYN_HOME = "dyn_home"  # back to the category list (edits the browser message in place)
DYN_PDF = "dyn_pdf"  # one PDF with a SINGLE category's dynamics (carries the category key)
DYN_CAT = "dyn_cat"  # open a category (carries the short category key, e.g. "blood")
DYN_IND = "dyn_ind"  # open one indicator's trend (category key + index into the sorted list)


def dyn_pdf(category: str) -> str:
    return f"{DYN_PDF}{_SEP}{category}"


def parse_dyn_pdf(data: str) -> str | None:
    parts = data.split(_SEP)
    return parts[1] if len(parts) == 2 and parts[0] == DYN_PDF else None


def dyn_category(category: str, page: int = 0) -> str:
    return f"{DYN_CAT}{_SEP}{category}{_SEP}{page}"


def parse_dyn_category(data: str) -> tuple[str, int] | None:
    parts = data.split(_SEP)
    if len(parts) == 3 and parts[0] == DYN_CAT and parts[2].isdigit():
        return parts[1], int(parts[2])
    return None


def dyn_indicator(category: str, index: int) -> str:
    return f"{DYN_IND}{_SEP}{category}{_SEP}{index}"


def parse_dyn_indicator(data: str) -> tuple[str, int] | None:
    parts = data.split(_SEP)
    if len(parts) == 3 and parts[0] == DYN_IND and parts[2].isdigit():
        return parts[1], int(parts[2])
    return None


def chart_pick(report_id: int, index: int) -> str:
    return f"{CHART_PICK}{_SEP}{report_id}{_SEP}{index}"


def parse_chart_pick(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, idx = rest.partition(_SEP)
    if head == CHART_PICK and rid.isdigit() and idx.isdigit():
        return int(rid), int(idx)
    return None


def chart_nav(report_id: int, index: int) -> str:
    return f"{CHART_NAV}{_SEP}{report_id}{_SEP}{index}"


def parse_chart_nav(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, idx = rest.partition(_SEP)
    if head == CHART_NAV and rid.isdigit() and idx.isdigit():
        return int(rid), int(idx)
    return None


def history_trend(report_id: int, index: int) -> str:
    return f"{HIST_TREND}{_SEP}{report_id}{_SEP}{index}"


def parse_history_trend(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, idx = rest.partition(_SEP)
    if head == HIST_TREND and rid.isdigit() and idx.isdigit():
        return int(rid), int(idx)
    return None


# --- Contextual consultation ("Запитати Дбайло") ---------------------------------
# A consult is anchored to a subject. The anchor is small (ids/index, < 64 B); the indicator's
# full series key + name are re-derived at tap time and held in FSM state, never in the callback.
CONSULT_CHART = "consult_chart"  # ask about ONE indicator (by carousel index into a report)
CONSULT_DYN = "consult_dyn"  # ask about ONE indicator (by index into a dynamics category)
CONSULT_REPORT = "consult_report"  # ask about a whole report's reading
CONSULT_SECTION = (
    "consult_sec"  # ask about ONE section of a report's reading (idx into SECTION_KEYS)
)
CONSULT_END = "consult_end"  # end the active consultation
CONSULT_REMIND = "consult_remind"  # open the "set a reminder" mini-flow during a consult (#4d)
CONSULT_REMIND_WHEN = "consult_rwhen"  # pick a relative offset for the reminder (carries days)
CONSULT_CLINICS = "consult_clinics"  # find transparent options of where to do an exam (#3)
CONSULT_RESUME = "consult_resume"  # back to the consultation from a sub-flow
# General-chat affordances (#6): from an ordinary companion reply, set a reminder / find where to do
# an exam — they enter a grounded GENERAL consultation, then reuse the consult reminder/clinic flow.
CHAT_REMIND = "chat_remind"
CHAT_CLINICS = "chat_clinics"


def consult_remind_when(days: int) -> str:
    return f"{CONSULT_REMIND_WHEN}{_SEP}{days}"


def parse_consult_remind_when(data: str) -> int | None:
    return _parse(CONSULT_REMIND_WHEN, data)


def consult_chart(report_id: int, index: int) -> str:
    return f"{CONSULT_CHART}{_SEP}{report_id}{_SEP}{index}"


def parse_consult_chart(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, idx = rest.partition(_SEP)
    if head == CONSULT_CHART and rid.isdigit() and idx.isdigit():
        return int(rid), int(idx)
    return None


def consult_dyn(category: str, index: int) -> str:
    return f"{CONSULT_DYN}{_SEP}{category}{_SEP}{index}"


def parse_consult_dyn(data: str) -> tuple[str, int] | None:
    head, _, rest = data.partition(_SEP)
    category, _, idx = rest.partition(_SEP)
    if head == CONSULT_DYN and category and idx.isdigit():
        return category, int(idx)
    return None


def consult_report(report_id: int) -> str:
    return _make(CONSULT_REPORT, report_id)


def parse_consult_report(data: str) -> int | None:
    return _parse(CONSULT_REPORT, data)


def consult_section(report_id: int, index: int) -> str:
    return f"{CONSULT_SECTION}{_SEP}{report_id}{_SEP}{index}"


def parse_consult_section(data: str) -> tuple[int, int] | None:
    head, _, rest = data.partition(_SEP)
    rid, _, idx = rest.partition(_SEP)
    if head == CONSULT_SECTION and rid.isdigit() and idx.isdigit():
        return int(rid), int(idx)
    return None


# --- Tier 1.3: button-menu section actions (static, no ids) ----------------------

# Hub destinations (the 🩺 Моє здоровʼя / 💊 Ліки й нагадування screens delegate to leaf helpers).
MENU_OPEN_ANALYSES = "menu_analyses"  # post the "Аналізи" hub as a new message (from Моє здоровʼя)
MENU_OPEN_GOALS = "menu_goals_sec"  # post the goals section (list + new)
MENU_OPEN_CHECKIN = "menu_checkin"  # start the grounded check-in dialog
MENU_OPEN_REMINDERS = "menu_reminders"  # open the reminders list
MENU_OPEN_MEMORY = "menu_memory_o"  # open the consult-memory view (a quick-jump from ❓ Довідка)

MENU_OPEN_LABS = "menu_labs_hub"  # back to the "Аналізи" hub (from the history list)
MENU_OPEN_HISTORY = "menu_hist"
MENU_GOALS_LIST = "menu_goals"
MENU_GOAL_NEW = "menu_goal_new"
MENU_PROB_LIST = "menu_probs"
MENU_PROB_NEW = "menu_prob_new"
MENU_MED_LIST = "menu_meds"
MENU_MED_NEW = "menu_med_new"
MENU_MED_PHOTO = "menu_med_photo"  # start the "read a prescription photo" flow
PRESCRIPTION_CONFIRM = "presc_ok"  # confirm the extracted meds (the meds live in FSM state)
MENU_PRICE = "menu_price"
MENU_COVERAGE = "menu_coverage"
# 💊 Ціна ліків proposes the user's own meds for a one-tap price check (by INDEX in the freshly
# re-derived medication list), plus ✏️ type-another.
PRICE_MED = "price_med"
PRICE_TYPE = "price_type"
# The one shared dialog-cancel callback (handled centrally; clears any active FSM).
CANCEL_DIALOG = "menu_cancel"


def price_med(index: int) -> str:
    return _make(PRICE_MED, index)


def parse_price_med(data: str) -> int | None:
    return _parse(PRICE_MED, data)


# --- Consult memory: grouped view + forget-all / forget-one (two-step confirm) ---
MEMORY_FORGET = "mem_forget"  # open the "забути все" confirmation
MEMORY_FORGET_OK = "mem_forget_ok"  # confirmed -> wipe this user's consult memory
MEMORY_FORGET_NO = "mem_forget_no"  # cancelled -> back to the groups list
MEMORY_HUB = "mem_hub"  # back to the conversation-groups list (static, edit-in-place)
# These carry the group's INDEX in the (re-derived) groups list — analyte / report / general groups
# share one address space, so a report_id no longer suffices (like the charts/problems picker).
MEMORY_GROUP = "mem_grp"  # open ONE conversation group (carries the group index)
MEMORY_FORGET_ONE = "mem_fgone"  # open the "забути цю розмову" confirmation (carries the index)
MEMORY_FORGET_ONE_OK = (
    "mem_fgoneok"  # confirmed -> forget just that conversation (carries the index)
)


def memory_group(index: int) -> str:  # the group's position in the groups list
    return _make(MEMORY_GROUP, index)


def parse_memory_group(data: str) -> int | None:
    return _parse(MEMORY_GROUP, data)


def memory_forget_one(index: int) -> str:
    return _make(MEMORY_FORGET_ONE, index)


def parse_memory_forget_one(data: str) -> int | None:
    return _parse(MEMORY_FORGET_ONE, data)


def memory_forget_one_ok(index: int) -> str:
    return _make(MEMORY_FORGET_ONE_OK, index)


def parse_memory_forget_one_ok(data: str) -> int | None:
    return _parse(MEMORY_FORGET_ONE_OK, data)
