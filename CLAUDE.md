# CLAUDE.md — Дбайло

Guidance for Claude Code working in this repo. Read `docs/dbaylo-discovery.md` for
the full vision; this file is the operational summary. **This is a docs-first build.**

## What this is

Дбайло ("the one who cares for you") is a personal health & wellness companion on
Telegram. **A caring friend with guardrails — not a doctor, not a prescriber.**
Single-user, personal use only (no productization — that is a different regulatory tier).

## Four-layer architecture

One product, two levels: a friendly wellness face on top, safety rails underneath.

- **L1 — Wellness companion** (`src/dbaylo/bot/`) — the daily Telegram face: goals,
  lightweight check-ins, evidence-based nudges, accountability. Built in Stage 3:
  `bot/companion_flow.py` + `companion/` (goals · checkin · reminders · scheduler ·
  conversation · symptoms), guarded by `wellness/` (see below).
- **L2 — Lab & data core** (`src/dbaylo/labs/`, `src/dbaylo/db/`) — lab intake, extraction,
  structured storage, a **deterministic** trend engine (never LLM output), and a separate
  LLM humanization layer. Built in Stage 2.
- **L3 — Triage** (`src/dbaylo/triage/`) — the deterministic red-flag safety core.
  **No LLM.** Pure functions, highest test coverage. This is the most important module.
  The **wellness guardrail** (`src/dbaylo/wellness/`) is its L1 sibling: a second
  deterministic safety core for disordered-eating / unsafe-goal escalation. Together
  these two cores own **all** escalation; the companion LLM never decides it.
- **L4 — Price & НСЗУ navigator** (`src/dbaylo/navigator/`) — on-demand med prices, МОЗ
  price-ceiling checks, НСЗУ coverage ("may be free — verify"), transparent provider
  aggregation. Built in Stage 4. Every free-text entry routes through `safety.gate`; all
  output passes the navigator guard. No price DB (on-demand + short-TTL cache).

## Safety rails — encoded in CODE, not just docs (non-negotiable)

These live in `src/dbaylo/triage/` and are enforced by `tests/triage/test_safety.py`:

1. **Not a doctor / not a prescriber.** Every triage outcome carries a disclaimer.
   Bot **output text** is scanned for dose directives (`safety.contains_dose_directive`).
   *Scope:* the guard inspects what Дбайло *says* — never DB field names. Storing what a
   doctor prescribed (`Medication.dose`/`schedule`) is record-keeping and is allowed.
2. **Triage asymmetry — escalate UP only.** `triage.engine.evaluate` returns
   `max(matched rule actions, floored at MONITOR)`. There is no code path that concludes
   "you can skip the doctor." Formalised by the **monotonicity** test: adding any symptom
   never lowers the action.
3. **No "skip the doctor" reassurance.** `safety.FORBIDDEN_REASSURANCES` is checked against
   every emitted message; `evaluate` runs every message through `safety.assert_safe_output`.
4. **No clinical-outcome claims / no "best doctor" ranking** (L4). Enforced by
   `navigator.guard.assert_safe_navigator_output` (rejects superlative provider
   recommendations) + a deterministic "reviews, not outcomes" label. Extended to price
   data: never a fabricated "free" (coverage exposes only `may_be_covered`) or "overpriced"
   (`CeilingStatus.NO_CEILING` for unregulated drugs). Named-drug boundary: `/price` never
   picks a drug for a condition.
5. **OCR never trusted silently** (L2) — always surface for confirmation, always keep
   the original file (`LabReport.source_file`).
6. **Friend, not sycophant; no crash diets / disordered-eating; beauty via health** (L1).
   Enforced by the `wellness/` guardrail (aggressive goal → REDIRECT; disordered-pattern
   text → SUPPORT) **and** by `safety.assert_safe_output`, which (Stage 3) also rejects
   restrictive-diet prescriptions (`contains_diet_prescription`: calorie/macro targets,
   fasting protocols). **Dose detection is re-anchored** to verb/intent as the primary
   signal (`contains_dose_directive`); a bare number+unit is only a weak secondary signal
   (`contains_dose_unit_mention`, never hard-fails) so benign companion numerics pass —
   `80 кг` and `1500 мл на день` pass, `500 мг/добу` and `приймай 2 таблетки` do not.
   The numeric boundary (forbidden: doses, ккал/macro targets, fasting; allowed: hydration,
   sleep hours, activity frequency) is encoded in both the guard and the companion persona.

When in doubt, **escalate toward care.** Never add prescribing logic or any autonomous
"skip the doctor" logic, anywhere. The LLM never emits restrictive numeric prescriptions
and never decides escalation — the two deterministic cores (triage, wellness) do.

## Language

**All user-facing bot text is Ukrainian** (command replies, triage messages,
disclaimer, errors). **Code stays English** — identifiers, enum tokens, rule ids,
docstrings, comments, this file. Every Ukrainian string lives in the single
module `src/dbaylo/locale.py`, so the safety guard and the tests read from one
source. The safety vocabulary is Ukrainian too: `locale.FORBIDDEN_REASSURANCES`
and `locale.DOSE_DIRECTIVE_PATTERNS` (the patterns require a dose object or a
number, so negated copy like the disclaimer's "не призначаю лікування" is safe).
When adding any user-facing string, put it in `locale.py` — never inline.

## Triage model (the core)

- `Symptom` (StrEnum) — controlled vocabulary in. No free text, no LLM in this layer.
- `Action` (IntEnum, ordered) — `MONITOR < SEE_DOCTOR < URGENT_CARE < EMERGENCY`.
  The **ordering is the safety mechanism** (`max` = escalate up).
- `TriageRule` — atomic; fires iff `triggers ⊆ report.symptoms` (AND within a rule;
  use multiple rules for OR). Seeded with kidney-stone red flags in `rules.py`.
- `evaluate(report) -> TriageOutcome` — the only entry point.

## Stack (locked — justify any new dependency)

Python **3.12** · aiogram 3 · FastAPI · SQLAlchemy 2.0 (**async**, aiosqlite) + Alembic
(sync) · SQLite · APScheduler (`AsyncIOScheduler`, wired in Stage 3) · matplotlib (charts). Config is
hand-rolled (`config.py` + python-dotenv) — lean by choice. **Any Claude calls go through the
`claude` binary via subprocess (Claude Code OAuth), NOT the Anthropic SDK** — only in
`src/dbaylo/llm/` and `labs/{extraction,humanize}.py`. Keep a `--dry-run` path for any external
action (`python -m dbaylo.labs.pipeline --dry-run <file>`). English-only code and comments.

## L2 — lab pipeline (Stage 2)

- **Extraction** (`labs/extraction.py`): `claude --print` reads the file (Read tool) and
  returns JSON constrained by the prompt (no `--json-schema`); a **defensive parser** tolerates
  fences/partial/malformed output and degrades to "ask the user", never crashes. Default model
  `sonnet`, escalates to `opus`; never `haiku`. **Captures the lab's OWN out-of-range indicator**
  per row (`out_of_range`, the boxed/highlighted "зона уваги" — OCR of the lab's verdict, not ours)
  and the report's overall `conclusion` (Stage 5). **Argv note:** `run_claude` ends its argv with a
  `--` terminator — `--add-dir`/`--allowedTools` are variadic and otherwise swallow the prompt (this
  silently broke every extraction once; `tests/test_llm_client.py` locks it). **Paged extraction**
  (`labs/pdf_split.py` + `extract_paged`/`extract_document`, the bot's entry): a multi-page PDF is
  split into `CLAUDE_EXTRACT_CONCURRENCY` (default 2) **contiguous chunks** — NOT one-per-page,
  because each `claude` start-up is costly — and the few chunks run in parallel, then `merge_reports`
  (pure) reassembles rows in order (exact dupes dropped) + first-seen metadata + joined narratives.
  A chunk failure is tolerated (some rows beat none); each chunk stays well under the per-page
  timeout. Measured ~38% faster on an 8-page panel (290s→179s); more RAM ⇒ raise concurrency ⇒
  closer to single-chunk latency. Single-page PDFs / images keep the one-call path.
- **Confirmation** (`bot/lab_flow.py`): extracted values (incl. report date & lab — a wrong date
  corrupts the series) are shown in Ukrainian and editable. **Nothing is written to the DB until
  the user confirms** (rail #2); pending values live in FSM state. The original file is always kept.
  **Duplicate guard** (migration 0010): the upload is SHA-256'd (`intake.file_hash` →
  `LabReport.content_hash`); if the same bytes were already CONFIRMED for this user
  (`find_confirmed_by_hash`), the bot skips extraction entirely and offers the saved report instead
  (a discarded/deleted upload does not block re-uploading).
  The confirmation view is **problems-first** (mirrors `/history` "Показники"): a bold header +
  summary (`N показників · ⚠️ K поза нормою`), then ONLY the rows that need a look — out of range OR
  unreadable (`❔`, `_is_unread`) — grouped by panel, with the in-range rows collapsed to
  `✅ Решта N — у межах норми`. A `📋 Усі N показників` button (`render_confirmation_full`,
  `_CB_SHOW_ALL`) expands the full numbered table on demand, so **every** value stays verifiable
  before saving (rail #5). An in-range row carries **no ✅ marker** (rail #4: a screen of green
  checks would imply "все добре"); the marker is **⚠️** out-of-range (`_is_oor`) / **❔** unreadable
  only. `📅 Дата` / `🔬 Лабораторія` buttons one-tap the two most-corrected fields (`_CB_EDIT_DATE`/
  `_CB_EDIT_LAB`); number-typing (`✏️ Виправити`) handles the rare value fix, using the **global**
  row index shown in either view. Rendered as escaped Telegram **HTML** (bold header/panels) via
  `answer_chunked(parse_mode=HTML)` (section-aware `split_for_telegram` as the overflow net); the lab
  `conclusion` is shown. Post-confirm, the analysis is sent first, then the follow-up offers are
  **sequenced ONE AT A TIME** (never a stack): repeat-lab reminder → (concern, only if out of range)
  → (charts offer, only if there is a real trend) — each a question shown only after the prior is
  answered (`_advance_after_repeat`/`_advance_after_concern`), never auto-opened. All offers are
  **stateless** (carry `report_id`) so they survive a restart / menu-tap state reset. **Charts are a
  PICKER, not a dump**: a real trend needs measurements on ≥2 distinct dates (a same-day re-upload is
  not a trend); the yes/no charts offer (`_charts_offer_keyboard`) opens — only on "Так" —
  `open_charts_picker`, where `history.list_report_trends` lists the trending analytes (flagged-first,
  ⚠️-marked) as one button each (paginated, `chart_pick`/`chart_page`), tapping one renders just THAT
  chart (`pipeline.render_one_chart`); `📊 Показати всі` (`chart_all`) stays as an opt-in dump. The
  picker is shared with `/history` (the card's 📈 → same `open_charts_picker`) — no flood anywhere.
  **Every chart reads the same way** (`charts.render_trend_chart`): a green band = acceptable range +
  red band(s) = out of range (drawn the SAME for two-sided / ≤X / ≥X), each point a green ● (in range)
  or red ✕ (out — labelled with its value, shape+colour so it survives colour-blindness), a
  legend (норма / у нормі / поза нормою), and the y-axis always spans the reference bounds (the band
  is never cut off; a flat series is not over-zoomed). Legend text is in `locale.CHART_LEGEND_*`.
- **Narrative documents** (Stage 6, migration 0007): a non-tabular medical document (МРТ/КТ/УЗД/
  висновок/виписка) is no longer rejected. Extraction returns `kind=narrative` with `report_type`,
  `narrative` (findings), and `conclusion` (no analyte rows); `ExtractedReport.is_narrative` routes
  the confirm view, `interpret()`, and `/history` rendering. `LabReport.kind`/`report_type`/
  `narrative` persist it; narrative reports are never fed to the trend engine (no LabResults).
- **Trend engine** (`labs/trends.py`): pure, deterministic, **no LLM/DB/network import** (enforced
  by a test). Direction is **range-relative** (`RETURNED_TO_RANGE`, `APPROACHING_RANGE`, …), never
  a health verdict (rail #4); IMPROVING/WORSENING `Polarity` is **internal only**. Series are
  grouped by a normalized analyte name + small `ANALYTE_ALIASES` map (known limitation: extend it).
  `classify()` adds a conservative **qualitative** flag (value vs ref text, NORMAL only on a clear
  match — never LOW/HIGH from free text); `is_out_of_range()` decides the ⚠️ marker.
- **Humanize / interpret** (`labs/humanize.py`): `humanize()` writes the trend summary; **Stage 5
  `interpret()`** gives an expert-level reading of a confirmed report — overall verdict (in DATA
  terms) **incl. how serious it looks**, per-flag "що може означати / до чого призведе" **+ a concern
  level**, grouped by system (e.g. білірубін+АЛТ → печінка), QUALITATIVE lifestyle+nutrition advice
  (**specific foods to favour/avoid**), and whether/how soon to see which doctor. Every output passes
  `assert_safe_output` (so no dose / restrictive-diet numbers / "skip the doctor" — and normalcy is
  phrased "у межах норми", never the forbidden "все добре") + disclaimer, with a deterministic
  fallback. The summary is stored on `LabReport`. **The four sections are generated as concurrent,
  focused `claude` calls** (`_interpret_parallel`, cap `CLAUDE_INTERPRET_CONCURRENCY`=3, per-section
  `CLAUDE_INTERPRET_TIMEOUT_S`=600) — ~40% faster than one serial call, and a hiccup costs only ONE
  section (it uses a deterministic fragment; the rest stay LLM). `_run_guarded` **retries once** on a
  transient `ok=False` or a guard-trip (a real timeout is not retried); only if EVERY section fails
  does it return the unified deterministic fallback. A **narrative** report keeps the single-call
  path (`_interpret_single`). Output carries light `*bold*`/`_italic_` markup → real `<b>`/`<i>` via
  `bot.formatting` (converted AFTER HTML-escaping; the guard reads the marker-stripped text so a
  forbidden phrase can't hide behind one; `/history` shows it stripped). **Delivery is a navigable
  drill-down** (not a 4-message wall): `formatting.split_interpretation` (pure) splits the stored
  summary on the canonical section headers, and `history_flow.send_analysis` shows ONLY the
  🩺 `Загалом` overview first, with a button per other section
  (`[⚠️ Показники][🌿 Що робити][🧑‍⚕️ До лікаря]`, `callbacks.history_interpret_view(report_id, idx)`)
  + refresh/delete. Tapping a section pulls just that part (its own P.S.), nav buttons travel with it
  (🩺 Огляд + the siblings). **Stateless** (re-derived from `LabReport.summary` by `report_id`, so it
  survives a restart; same in post-confirm and `/history → 🔬 Розбір`, retroactive on cached
  summaries). A narrative/deterministic reading lacks the 4-section shape → falls back to the whole
  text. Rows are fed **grouped by panel** (`ExtractedAnalyte.section`
  / `LabResult.section`, migration 0009): a combined blood+urine report keeps its groups apart in the
  confirm view, `/history`, and the interpretation, so a name in two panels (Глюкоза, Лейкоцити) is
  never confused.

## L1 — companion (Stage 3)

- **Wellness guardrail** (`wellness/`): the L1 safety core, a sibling of triage. Pure,
  deterministic, **no LLM/DB/network import** (enforced by a test). `Concern` (IntEnum:
  `OK < REDIRECT < SUPPORT`) escalated **up only** via `max`, floored at `OK` (monotonicity
  test). Goal-parameter rules (weight-loss rate > 1.0 kg/week → REDIRECT, general non-clinical
  framing) + Ukrainian text signals (disordered patterns → SUPPORT). Its message runs through
  `assert_safe_output`.
- **Goals** (`companion/goals.py`): a goal is validated through the guardrail **before** it is
  accepted — only an `OK` verdict persists a `Goal`; REDIRECT/SUPPORT returns guidance, stores
  nothing.
- **Symptom handoff** (`companion/symptoms.py`): deterministic Ukrainian keyword → `Symptom`
  → `triage.evaluate`. The LLM never makes the escalation call. `SYMPTOM_KEYWORDS` is kept
  **disjoint** from the wellness purging signals (involuntary vs. self-induced vomiting) so
  triage's earlier pass can't mask a purging signal.
- **Check-in** (`companion/checkin.py`): gentle evening prompt; lenient parse of
  sleep/water/mood/training; symptoms route to triage. One follow-up only, never nags
  (`should_send_nudge`). `--dry-run` prints the prompt.
- **Reminders + scheduler** (`companion/{reminders,scheduler}.py`): `Reminder` rows are the
  **source of truth**; `schedule` is `cron:<expr>` or `date:<iso>`. The live `ReminderScheduler`
  rebuilds one job per active row on startup *and* lets handlers `schedule`/`unschedule` a row
  without a restart (stored in `dispatcher["reminder_scheduler"]`). `next_run` is **not stored**
  (read from the scheduler). **Durable across a restart** (Stage 6): every fire records
  `Reminder.last_fired_at` (migration 0008), and `start()` runs a **catch-up** that delivers any
  occurrence due since that anchor while the process was down — bounded by `MAX_CATCHUP` (12h),
  coalesced to one delivery per reminder (`last_due_occurrence`); an overdue one-off is delivered
  then retired, a future one just scheduled. `create_reminder` anchors `last_fired_at` at creation
  so a new reminder is never caught up for an occurrence before it existed. `--dry-run` lists jobs
  without firing. Medication reminder text never carries a dose.
- **Tier 1.1 — proactive behavior** (`companion/{concerns,medications,proactive,callbacks}.py`,
  `bot/proactive_flow.py`): the check-in is **conditional** — a daily check-in is scheduled **iff**
  ≥1 active `Condition` exists (`ConditionStatus`, migration 0004), never an unconditional ping.
  `proactive.add_problem` schedules it on the first concern; resolving the last removes it
  (`reconcile` self-heals on startup). The firing check-in also asks "still relevant?" for concerns
  due for review (~7 days, `Condition.last_review_at`) in ONE **batched** message — a `✅ <name>`
  button per due concern (not a message each), and `keyboards.remove_button_row` drops only the
  tapped concern's row so the rest stay actionable. Commands: `/problem`,
  `/problems` (resolve/rename), `/medication` (name + times → one reminder per time, **no dose**,
  `Reminder.medication_id`; turning a medication off removes *all* its jobs), `/reminders`
  (list + turn off, next_run from the scheduler). On lab confirm the bot **offers** a repeat-lab
  reminder ([1м][3м][6м][Інше][Ні]) and, if a value is out of range, offers a draft concern
  (rename later). `/start` now captures `telegram_id`. Reminders go only to the owner (owner lock).
- **Tier 1.2 — history & retrieval** (`companion/history.py`, `bot/history_flow.py`, migration
  0005): browse stored labs as a **master-detail UI** (progressive disclosure, not a wall). `/history`
  (alias `/reports`) sends ONE message — a paginated list (`_list_view`, 8/page, `◀ ▶`) of **one
  button per confirmed report** (`📅 date · lab · N⚠️k`). Tapping a report **edits the message in
  place** into its **card** (`render_card`) with `[🔬 Розбір][📊 Показники] [📈 Динаміка][📄 Файл]
  [🗑 Видалити][◀ Назад]`; Назад/pager edit back (no message spam). **🔬 Розбір is CACHED** — shows
  `report.summary` instantly if present, else generates once via `reconstruct_report`→`interpret`
  and stores it; `[🔄 Оновити][🗑 Видалити розбір]` regenerate / clear it. **📊 Показники is
  problems-first** (`render_problems`): the lab conclusion + ONLY the out-of-range rows (grouped by
  panel) + an aggregate `✅ Решта N — у межах норми`, with `[📋 Усі показники]` (opt-in full table)
  and `[📈 Динаміка]`. **📈 Динаміка** opens the shared **charts picker** (`open_charts_picker`):
  one button per trending analyte (flagged-first), tap one → its single chart — no 85-chart flood;
  any indicator also via `/trend <name>`. Every health view ends with the **P.S. disclaimer** (`_ps`
  / `locale.HIST_PS_BLOCK`).
  Optional filters parse deterministically (lab keyword / known lab, `YYYY-MM(-DD)`, year,
  Ukrainian month, `останній`). `/trend <analyte>` reuses the
  deterministic trend engine (chart when ≥2 points). **All retrieval is no-LLM** — listing,
  rendering, parsing are pure. The NL search is the one seam: a free-text turn is routed to history
  only when `is_history_query` (intent **and** a concrete token); the handler calls
  `safety.screen()` **first**, then `parse_history_query`, and **falls back to the companion** when
  no concrete filter survives (never shows an empty result / steals normal chat). **Delete is
  two-step**, shows exactly what is removed, and cleans up Tier 1.1 **couplings**: `Condition` /
  repeat-lab `Reminder` rows now carry a nullable `report_id` FK (`SET NULL`), so deleting a report
  resolves its proposed concern and retires its repeat-lab reminder (shown in the confirmation; the
  nightly backup is the safety net). An opt-in `🧹` footer purges orphaned PENDING (>1h) / DISCARDED
  uploads + their files. Callbacks carry ids only (well under 64 B); analytes are looked up by index.
- **FSM hygiene** (`bot/state_reset.py`): a global `CommandStateResetMiddleware` (message-level
  **outer** middleware, registered in `build_dispatcher` after the owner lock) aborts any in-progress
  dialog when a `/command` arrives — it clears the FSM state **and** resyncs `raw_state` *before*
  handler resolution, so a command is never consumed as a dialog's text answer. Paired with a
  per-handler rule: **blank/whitespace input never creates a record** (goal · problem · medication ·
  check-in answer with `locale.NOTHING_SAVED`). `python -m dbaylo.maintenance.cleanup_phantoms`
  removes phantom rows (blank or `/`-leading name/target) and retires a now-pointless check-in.
- **Tier 1.3 — button menu** (`bot/menu_flow.py`, `bot/keyboards.py`): a **UI/entry layer only**, no
  new domain logic. A persistent `ReplyKeyboardMarkup` (📊 Аналізи · 🎯 Цілі · ⚕️ Проблеми · 💊 Ліки ·
  🔔 Нагадування · 💰 Ціни/НСЗУ · 📝 Чек-ін · ❓ Довідка) is shown from `/start`; the native "/" command
  menu is populated on startup (`app.apply_bot_commands` from `locale.BOT_COMMANDS`, `set_my_commands`)
  so **no command must be typed from memory** — a parity test (`tests/test_bot_commands.py`) fails if any
  `Command(...)` handler lacks a "/" menu entry. Each label opens a section screen
  (message + inline actions) that **delegates to reused helpers** — the commands are now aliases over
  the same `open_*` / `start_*_dialog` helpers (`companion_flow` · `proactive_flow` · `history_flow` ·
  `navigator_flow`). Menu labels are matched by **exact equality** (`F.text == locale.MENU_*`,
  `StateFilter(None)`) in the `menu` router registered **before** history-NL/companion, so a tap never
  leaks into chat; `locale.MENU_LABELS` is also a reset trigger in `CommandStateResetMiddleware`
  (message-level only — callbacks keep their own cancel) so a label tap mid-dialog aborts it. Every
  FSM dialog carries a shared inline `[Скасувати]` (`callbacks.CANCEL_DIALOG`, one central handler →
  clears any state, saves nothing). `/price`·`/coverage` gained a small `NavStates` so the **typed**
  answer routes through `run_price`/`run_coverage` (i.e. `gate.screen`) **identically to the arg** — a
  symptom in the drug field short-circuits to triage. No new models/migrations.
- **Conversation** (`companion/conversation.py`): companion LLM via `llm/client.py`. Every reply
  passes `assert_safe_output` + disclaimer, with a deterministic Ukrainian fallback. The persona
  forbids fabricated sources/statistics and encodes the numeric boundary.
- **Symptom intake** (`companion/intake.py`, Stage 6B): a multi-turn **history-taking** interview —
  when a free-text turn is a symptom (gate→triage) or a broad physical complaint
  (`looks_like_complaint`, router-only), `companion_flow` starts a guided intake (FSM
  `IntakeStates.in_progress`, bounded by `MAX_TURNS`). `intake.advance` re-runs `safety.screen`
  every turn: the **deterministic triage still owns escalation** — a `URGENT_CARE`/`EMERGENCY` red
  flag (or a disordered-eating guardrail signal) **leads** the reply verbatim and the LLM can never
  lower it. Output passes `assert_safe_output` + disclaimer, deterministic fallback. Imports
  `safety.screen` + `run_claude` (gate-routed; never the escalation engines) so the AST choke-point
  stays green. **FSM state is persisted** (`bot/storage.py` `SQLiteStorage` — a dedicated SQLite file
  via the already-present `aiosqlite`, wired in `build_dispatcher`) so an in-progress interview /
  lab confirmation survives a restart; the file is separate from the domain DB so Alembic is unaffected.
- **Safety gate** (`safety/gate.py`, Stage 3.5): the **single sanctioned path from user text to
  the LLM**. `screen(text, *, goal=None) -> GateDecision` encodes the one canonical order —
  symptoms→triage, else wellness guardrail, else cleared→LLM (precedence: a symptom outranks a
  disordered-eating signal; the chain short-circuits on the most acute match). All four entry
  points (conversation, free-text, check-in, goals) route through it; nothing re-implements the
  order inline. Pure orchestration — no LLM/DB/rules. An import-graph test
  (`tests/safety/test_gate_is_choke_point.py`) fails if a future handler reaches `llm/client`
  without the gate or imports an escalation entry point (`triage.evaluate`, `wellness.evaluate`)
  directly. `companion/symptoms.py` now only *detects* tokens (`detect_symptoms`); the triage call
  lives in the gate.

## L4 — price & НСЗУ navigator (Stage 4)

- **Entry + gate** (`navigator/pipeline.py`): `/price` (named drug) and `/coverage` (service).
  Command args are user text — `run_price`/`run_coverage` call `gate.screen` FIRST, so a symptom
  short-circuits to triage before any fetch/LLM. **The only navigator module that imports
  `run_claude`** (the Claude fallback is invoked post-gate). `--dry-run` runs the pipeline over a
  built-in HTML fixture (no network).
- **Fetch** (`navigator/fetch.py`): async `httpx` (the one new runtime dep), fail-soft (a dead
  source returns `ok=False`, never raises/fabricates), descriptive UA, short-TTL on-disk cache,
  on-demand only — **no price DB**.
- **Sources** (`navigator/sources/`): per-site deterministic parsers (mypharmacy, doc.ua, robots-
  permissible) — a parse miss yields `[]`, never a guess. **tabletki.ua / apteki.ua are
  declared-disabled** (verified robots-hostile) and never fetched. `extract.py` is the Claude
  fallback (prompt + pure parser; **no `run_claude` import** here) — its prices are sanity-checked
  and marked "перевір".
- **Coverage** (`navigator/coverage.py`): НСЗУ open data, facility-level. The type **cannot express
  a categorical "free"** — only `may_be_covered` + a verify link ("може бути безкоштовно за ПМГ —
  перевір"). Coverage is checked **before** price.
- **Ceiling** (`navigator/ceiling.py`): МОЗ regulated prices (reimbursement subset only).
  `CeilingStatus.NO_CEILING` is first-class — for an unregulated drug we say "немає регульованої
  стелі", never a fabricated "overpriced".
- **Providers** (`navigator/providers.py`): transparent attributes, reviews *as reviews*, no
  ranking. The "Це думки пацієнтів, а не результати лікування" label is attached **deterministically
  by the render template** (not the LLM); `assert_provider_labeled` is the last net.
- **Guard** (`navigator/guard.py`): `assert_safe_navigator_output` = no "skip the doctor"
  reassurance + no diet prescription + **reject superlative provider recommendations** (rail #4:
  "найкращий хірург", "оперуйтесь у", "гарантований результат"). `is_drug_recommendation_request`
  enforces the named-drug boundary (rail #1): "/price" never picks a drug for a symptom/condition.
  (The dose-directive check is intentionally *not* applied — product names cite dose-form tokens;
  the navigator never advises a dose.)

## Layout

```
src/dbaylo/  triage/ (L3)  wellness/ (L1 guardrail core)  safety/ (gate: the user-text choke-point)
             labs/ (L2)  navigator/ (L4)  llm/ (claude subprocess)  db/  web/  locale.py  config.py
             bot/ (handlers · menu_flow · keyboards · *_flow · access · state_reset)  maintenance/
             companion/ (L1 face: goals·checkin·conversation·symptoms · reminders·scheduler·
                         concerns·medications·proactive·callbacks · history · intake)
migrations/  Alembic 0001..0010   tests/  triage·labs.trends·wellness·safety·navigator.guard: highest bar
```

## Dev commands

```bash
venv/bin/python -m pytest --cov   # tests + coverage (gate >= 90% on the deterministic safety surfaces)
venv/bin/ruff check src tests     # lint        venv/bin/ruff format src tests
venv/bin/mypy                     # strict type check
venv/bin/alembic upgrade head     # apply migrations to the DB
venv/bin/alembic revision --autogenerate -m "msg"   # new migration after model changes
venv/bin/dbaylo-web               # serve FastAPI (/health, /webhook/{token})
venv/bin/dbaylo-bot               # run the bot via long polling (needs BOT_TOKEN)
venv/bin/dbaylo-scheduler --dry-run                            # list reminder jobs, fire nothing
venv/bin/python -m dbaylo.companion.checkin --dry-run          # print the check-in prompt
venv/bin/python -m dbaylo.labs.pipeline --dry-run lab.jpg      # extract only, no DB/Telegram
venv/bin/python -m dbaylo.navigator.pipeline --dry-run парацетамол   # price a drug from a fixture
venv/bin/python -m dbaylo.maintenance.cleanup_phantoms --dry-run     # list phantom rows, delete nothing
```

After any model change: regenerate a migration and run `alembic check` (must report no drift).

## Roadmap

Stage 1 (done): skeleton + safety core. Stage 2 (done): lab intake + Claude extraction +
OCR-confirm loop + deterministic trends + charts + humanized summary. Stage 3 (done): goals,
daily check-in, reminders (APScheduler, DB-as-source-of-truth), companion chat, the wellness
guardrail. Stage 3.5 (done): the `safety.gate` choke-point. Stage 4 (done): price & НСЗУ
navigator (med prices, МОЗ ceiling, НСЗУ coverage, transparent providers). All roadmap layers
shipped. **Tier 0 (done):** owner lock + off-box backups. **Tier 1.1 (done):** proactive behavior —
conditional check-in (active concerns), medication & repeat-lab reminders, reminder management, live
`ReminderScheduler`. **Tier 1.2 (done):** history & retrieval — `/history`·`/reports`·`/trend`,
original-file + stored-results access, deterministic NL search (gate-first, companion fallback),
two-step delete with Tier 1.1 coupling cleanup, opt-in orphan purge. **Stage 5 (done):** lab
interpretation & advice — extraction captures the lab's own out-of-range indicator + conclusion,
⚠️/✅ flags (no stray ❔), and `interpret()` gives an expert reading + qualitative recommendations
(guard-backed). **Stage 6 (done):** narrative/imaging documents (МРТ/УЗД/висновок — read, confirm,
expert summary, /history) + conversational symptom intake (history-taking with the deterministic
triage as the non-negotiable escalation backstop). **FSM-cancel fix (done):**
commands abort in-progress dialogs; blank input never persists; phantom-row cleanup CLI. **Tier 1.3
(done):** button menu — persistent reply keyboard + section screens delegating to reused flow helpers,
shared `[Скасувати]`, menu labels reset state, navigator FSM gated like the command arg (UI layer only,
no new models).
