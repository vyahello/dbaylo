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
   picks a drug for a condition. **OWNER-AUTHORIZED EXCEPTION (personal bot only):** the
   consult clinic finder (`consult.find_clinics`, the 🏥 button) web-searches REAL clinic
   options (name · address · contacts · public rating) for an exam in the user's city, with
   an honest "options from open sources; ratings are opinions, not outcomes — verify" frame.
   It is the ONE place provider ranking is allowed; the OTHER rails still hold (no
   dose/diagnosis/skip-doctor; a red flag in the query still escalates via the gate), and the
   navigator's own guard is unchanged.
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
  and the report's overall `conclusion` (Stage 5). Also classifies the upload via `document_type`
  ("lab" | "prescription") for **auto-routing** a freely-dropped рецепт to the meds flow (see L1
  💊 з фото рецепта). **Argv note:** `run_claude` ends its argv with a
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
  **stateless** (carry `report_id`) so they survive a restart / menu-tap state reset.
  **Auto-recovery of an interrupted analysis**: the summary is set to PENDING (`summary == ""`,
  `history.SUMMARY_PENDING`) right BEFORE the slow LLM call and to the real text after — so an empty
  summary uniquely means "a restart killed the interpretation" (distinct from `NULL` = never
  analysed / розбір deleted). On startup `app.recover_interrupted_analyses` finds those reports
  (`history.find_interrupted_analyses`) and offers the owner a one-tap **▶️ Доробити розбір**
  (= `history_interpret`, which regenerates because the summary is empty); applied at confirm AND
  the `/history` regenerate path, best-effort (never blocks startup). **Charts are a
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
  match — never LOW/HIGH from free text); `is_out_of_range()` decides the ⚠️ marker. **References &
  qualitative values are persisted** (migration 0011: `LabResult.value_text`/`ref_text`): the model
  often leaves a one-sided range as free text, so `labs/refparse.parse_ref_range` derives numeric
  bounds at extraction ("< 5.2"→high=5.2, "до 50"→high=50, "X-Y"→both) — without this ~40% of rows
  had no `ref_low/ref_high` and the chart could draw no norm band. **Trend charts honour the lab's
  own flag**: a point is red when `LabPoint.flagged` (the lab's out-of-range mark, reliable even with
  no numeric ref) OR numerically out of band, so a flagged value shows red even when the band is
  absent (`charts.render_trend_chart`). The **dynamics browser lists only analytes with a real numeric
  trend** (≥2 numeric measurements) — qualitative urine analytes (0 numeric values) are seen
  per-report in `/history`, not in the trends browser. NOTE: existing rows predate 0011, so the band
  fully returns only on re-upload / re-extraction.
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
  nothing. **🎯 Цілі is an agent screen** (`companion_flow.open_goals_screen`, shared by the menu tap
  and `/goals`): `propose_goals` suggests a `Привести <name> до норми` goal per current out-of-range
  finding (name via `HealthFinding.display_name` — specimen-disambiguated) + generic wellness goals,
  EXCLUDING any goal the user already has at ANY status (`known_goal_texts` — adopted/achieved/removed
  are never re-suggested). `propose_goals` returns `GoalSuggestion(text, subject, series_key)`.
  **The screen is a MASTER-DETAIL** (long "Привести … до норми" was cut off on mobile): the master
  (`_goals_master`) lists SHORT subject buttons — 🎯 suggestions (`goal_view_sug` by index) then 📌
  adopted goals (`goal_view` by id) — and a tap opens that goal's **detail**, which shows the FULL
  title + (for a data goal) the indicator's history "коли були поза нормою".
  **Each goal carries its clinical-group emoji** (🩸/🔬/⚗️/🧬/🧫…) via `grouping.category_emoji`
  (the single source of truth for name→group, shared with `proactive_flow._category_prefix`), so a
  blood `Еритроцити (RBC)` reads apart from a urine `Еритроцити (сеча)`; a generic wellness goal
  (Сон/рух) carries none. And the master no longer shows **empty** `💡 Пропоную взяти:` / `📌 Твої
  активні цілі:` headers — every suggestion / adopted goal is ALSO a text line under its header
  (`GOAL_MASTER_ITEM_LINE`, full goal + group emoji), with the buttons as the tap targets.
  The detail's history is `health.indicator_history` over `HealthFinding.series_key`; a goal target
  is mapped back to its finding by `goals.goal_analyte`/`target_subject`. The ACTION lives in the
  detail: a suggestion has
  `[🎯 Взяти ціль]` (`set_goal`, guardrail re-vets), an adopted goal has `[✅ Досягнута]`
  (`achieve_goal`→ACHIEVED) `[🗑 Прибрати]` (`remove_goal`→ABANDONED, the **undo for an accidental
  adopt**); every detail has `[◀ Назад]`, every action edits back to the master. No migration (reuses
  `GoalStatus`). **Goals are FUNCTIONAL, not a dead list** (the owner found them inert): an active
  goal is grounded into `consult_context.patient_profile` ("Goals the user is actively working
  toward — support these…") so the companion / consult / intake / check-in all REFERENCE and support
  it; and `health.should_have_checkin` returns True for an active goal (a goal alone turns ON the
  daily check-in), with `on_goal_adopt`/`achieve`/`remove` calling `proactive.reconcile_checkin` so
  the job appears/retires immediately. The check-in persona asks how a goal is going (no numeric
  targets, the rails hold).
- **Symptom handoff** (`companion/symptoms.py`): deterministic Ukrainian keyword → `Symptom`
  → `triage.evaluate`. The LLM never makes the escalation call. `SYMPTOM_KEYWORDS` is kept
  **disjoint** from the wellness purging signals (involuntary vs. self-induced vomiting) so
  triage's earlier pass can't mask a purging signal.
- **Check-in** (`companion/checkin.py`): lenient parse of sleep/water/mood/training; symptoms route
  to triage. **The answer CONTINUES into a real conversation — it does NOT dead-end at "Занотував"**:
  `companion_flow.on_checkin_answer` logs the state (`process_checkin` — sleep/mood/their words →
  state memory) SILENTLY, then routes the text through the SHARED `_engage_with_text` (extracted from
  `on_free_text`): a symptom / complaint opens the history-taking **intake** (clarifying questions +
  triage backstop + next steps), else a grounded companion reply. So answering the check-in is a
  conversation starter, not a one-shot logger. `intake.looks_like_complaint` was **widened** beyond
  the pain vocabulary to catch pressure/heaviness ("тисне"/"важкіст"), region ("поперек"), colic/
  spasm/aching, and "камінь/камені" (kidney/gallstone) — phrasings that slipped past it (the owner's
  "вийшов камінь з нирки" check-in reply got only "Занотував"). When the intake CONCLUDES
  (`_run_intake_turn`, `reply.done`), it attaches `chat_affordance_keyboard()` (🔔 Нагадати ·
  🏥 Де зробити) so "що робити далі" is one tap. **One follow-up ~90 min after the prompt
  (`_fire_nudge`) ALWAYS fires now** (owner wanted the second daily touch unconditional), but its TEXT
  is context-matched by `has_checkin_on`: `CHECKIN_NUDGE` ("я тут, якщо захочеш…") when no check-in
  arrived yet, else `CHECKIN_FOLLOWUP` ("як ти зараз? щось змінилося? можеш не відповідати") so it
  never reads as "haven't heard from you" guilt. (`should_send_nudge` is kept as a tested helper but
  no longer gates the send.) The scheduled 10:00 prompt itself fires unconditionally — a same-day
  manual check-in never suppresses it (locked by a scheduler test). The firing prompt is now
  **GROUNDED + proactive** (`build_grounded_prompt`, LLM + `assert_safe_output`, deterministic
  fallback to the static `build_prompt`): it opens by asking about the user's ACTUAL current
  concerns/data, like an assistant who knows them. `--dry-run` prints the static prompt. The MANUAL
  📝 Чек-ін (`companion_flow.start_checkin_dialog`) shows a `CHECKIN_ANALYZING` placeholder the moment
  it is tapped and EDITS it into the grounded prompt when ready (the multi-second LLM call otherwise
  reads as "waiting for nothing"); the grounded prompt now also references the user's active GOALS.
  **Tracked-concern FOCUS** (`health.checkin_focus_block`, deterministic): so "Під наглядом" is FELT,
  each check-in is handed ONE tracked concern (rotated daily by `today.toordinal() % N`) named
  EXPLICITLY, and — if its latest measurement is `>= CHECKIN_RETEST_DAYS` (90) old — a re-test nudge
  (the concern is matched to its analyte via `_already_known` to read `last_date`); the persona LEADS
  the check-in with it. The focus only says WHICH concern + WHETHER to re-test; the LLM phrases it.
  **The manual button and the scheduled 10:00 check-in are built IDENTICALLY**: both ground via the
  shared `checkin.full_checkin_context` (= `grounded_context` [profile+concerns+findings+state] + the
  focus block), so the 📝 button is not a poorer version of the automatic one (the focus used to be in
  the scheduled `checkin_messages` only). Each day's check-in is GENERATED FRESH (rotating focus,
  updated recency/state, non-deterministic LLM phrasing) — same engine, never the same words twice;
  the scheduled one ALSO carries the ~weekly "still relevant?" review batch.
- **Health analyzer** (`companion/health.py`, the "big idea" foundation): **deterministic, NO LLM,
  NO diagnosis** (rail #4) — scans ALL confirmed labs through the trend engine into `current` (latest
  out of range), `watch` (still in range but trending toward — within 15% of — a bound: an EARLY
  WARNING), and `resolved` (was off, latest back in range — "remembered, not dwelt on").
  `build_health_context` (profile + current + watch + resolved) GROUNDS the companion chat, the
  symptom intake AND the proactive check-in (so "болить поперек" connects to the real kidney history;
  the check-in asks about the real flag, gently flags a worsening trend, and — using the dates in the
  context — nudges re-testing a months-old flag). `should_have_checkin` = active concern OR
  `has_current_flags` (conservative: a `watch` alone does not trigger a check-in, only enriches it).
  Phrasing is downstream + always guarded; the analyzer itself only states the numbers.
  **State memory** (`checkin.state_memory_context`, `CheckIn.note`, migration 0016): recent check-ins
  (sleep/mood/symptoms + the user's own words) are remembered so Дбайло notices the dynamic and
  follows up. `checkin.grounded_context` = the lab picture + this state memory, shared by the
  check-in and the companion/intake (via `companion_flow._health_context`).
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
  `health.should_have_checkin` (≥1 active `Condition`, `ConditionStatus`, migration 0004 — OR a
  currently out-of-range indicator), never an unconditional ping. `proactive.reconcile_checkin`
  (used by `add_problem`/`resolve_problem`, the lab-confirm hook, `delete_report`'s final pass, and
  the scheduler's startup `reconcile`) makes the live job match that condition. The firing check-in also asks "still relevant?" for concerns
  due for review (~7 days, `Condition.last_review_at`) in ONE **batched** message — a `✅ <name>`
  button per due concern (not a message each), and `keyboards.remove_button_row` drops only the
  tapped concern's row so the rest stay actionable. **⚕️ Проблеми is AGENT-DRIVEN and grouped**
  (the menu tap → `open_problems` directly, no sub-menu): `health.propose_problems` reads ALL labs;
  the screen is a **category master-detail** (`proactive_flow._problems_top`) — a digest, never a
  wall. The top level shows ONE button per clinical category that has something out of range
  (`grouping.categorize` → `🩸 Кров — 3`, `🔬 Сеча — 2`, …, `CATEGORY_ORDER`), then `📈 На межі — N`
  (the `watch` findings in their own group, NOT mixed with problems), `✅ Вже відстежую — N`, and —
  only if any exist — `🙈 Приховані — N`, plus `➕ Своя проблема`. Tapping a category opens its
  `_category_detail` (edit-in-place): each out-of-range finding as a line + `[👁 <name>][✖]` (the
  name is ON the track button — stacked rows must not read as identical "Відстежувати"),
  `[◀ Назад]`. **Track/dismiss callbacks carry `(category, flat-index)`** (`callbacks.problem_track`/
  `problem_dismiss` → `parse_*` returns a tuple) — the index addresses the finding in the
  freshly-derived **flat** `propose_problems` list (re-resolved on tap, like the charts picker); the
  category is only so the SAME detail re-renders after the action (empty → falls back to top). 👁 =
  `add_problem`; ✖ = `dismiss_problem` → a `ConditionStatus.DISMISSED` row (migration 0017), no longer
  re-proposed nor keeping the data-driven check-in alive (`has_current_flags` skips dismissed).
  **The screens EXPLAIN the benefit + give persistent feedback** (the owner found tracking opaque —
  items vanished with no destination, the value unclear): the headers (`PROBLEM_CAT_HEADER`,
  `PROBLEM_TRACKED_HEADER`, `PROBLEM_GROUP_HEADER`) spell out what 👁 does (nags in the daily check-in
  + grounds the chat); `_act_on_proposal` returns a persistent `note` (`PROBLEM_TRACK_NOTE`/
  `PROBLEM_DISMISS_NOTE`, prepended by `_edit_to_detail`/`_edit_to_top`) so a tapped finding shows
  "взяв «X» під нагляд → у ✅ Вже відстежую" in-message, not just a flash toast; the broken resolve
  message is fixed (`PROBLEM_RESOLVED`).
  **✖ is reversible**: a dismissed finding lives under `🙈 Відкладені` with `[↩️ <name>]` →
  `proactive.restore_problem` (`concerns.undismiss` + reconcile) re-proposes it. The `🙈` section
  shows ONLY dismissals that are STILL off (`health.list_relevant_dismissed` — a waved-off finding
  that returned to range is stale and omitted, so the section appears only with something real to
  restore). **✅ resolve is also reversible** (the owner: "якщо жму галочку він пропадає"): resolving
  a tracked concern (`concerns.resolve` → RESOLVED) lands it in a `✔️ Вирішені — N` archive
  (`concerns.list_resolved`, `_resolved_detail`), each row `[↩️ <name>]` → `proactive.reopen_problem`
  (`concerns.reopen` → ACTIVE + reconcile) puts it back under nadhliad — so a closed concern is never
  lost. The Під наглядом / Відкладені / Вирішені lists **show each item's clinical GROUP** (🩸/🔬/⚗️/
  🧫) re-derived from the STORED name — `proactive_flow._category_prefix` runs `grouping.categorize`
  over the name as both section+analyte (so "Аналіз крові: …", a "(сеча)" tag, etc. are caught;
  custom non-lab concerns → no tag), and `_by_category` sorts them so blood items cluster, then
  urine, … (no migration — derived from the name). The 📈 **На межі list MIXES specimens**, so it
  tags every item with its sample via
  `HealthFinding.specimen_name` (blood→`(кров)` too, not just urine/semen) — "Базофіли" / "ГГТ"
  aren't ambiguous next to "Неплаский епітелій (сеча)". **Names are
  specimen-disambiguated**: a finding carries `category` + `specimen` (`trends.specimen`); the
  persisted/shown name uses `HealthFinding.display_name` so a urine `Еритроцити (сеча)` is never
  confused with the blood one, and `health._already_known` is **specimen-aware** (tracking blood
  Еритроцити no longer suppresses proposing the urine one). Tracked concerns sit behind `✅ Вже
  відстежую` (`[✅ <name>][✏️]` resolve/rename). Commands: `/problem`,
  `/problems` (resolve/rename), `/medication` (name + schedule → one reminder per time, **no dose** in
  the reminder, `Reminder.medication_id`), `/reminders` (list, next_run from the scheduler).
  **The bot splits the day itself** (`medications.parse_frequency`/`distribute_times`/`resolve_schedule`):
  a doctor prescribes a FREQUENCY ("3 рази на день", "2 таблетки 3 рази"), NOT clock times, so the
  add-med dialog asks "скільки разів на день?" and spreads N intakes over a deterministic waking-hours
  schedule (1→09:00 … 3→08/14/20 … capped at `MAX_PER_DAY`=6); explicit "08:00, 20:00" still works, and
  a per-intake amount ("2 таблетки", `parse_dose`) is captured as `Medication.dose` record-keeping. The
  prescription-photo flow applies the SAME spread (`prescription_flow._with_resolved_times`): a script
  read as frequency-only is now auto-scheduled, not skipped for manual entry. **💊 Список ліків is a
  master-detail** (`_medications_payload`): a short `💊 <name>` button per LIVE medication (one with
  an active reminder) OPENS its card (`medication_view`, never a destructive turn-off tap); the card
  (`_med_card`, HTML) shows name · **dose** (record-keeping, escaped) · times · next run, and the
  ACTION is a deliberate `[🔕 Вимкнути нагадування]` (`turn_off_medication` — keeps the Medication row
  + dose, just deactivates the jobs) `[◀ Назад]`. The med card is shared by the 💊 meds list and the
  🔔 reminders list, so `medication_view`/`medication_off` carry an **origin** ('m'/'r') and «Назад» /
  the turn-off return to the list it was opened from (`MED_LIST_BACK` vs `REMINDERS_BACK`).
  **The prescription PHOTO is kept and re-openable** (migration 0019, `Medication.source_file`): a med
  read from a prescription photo stores that file's path, and the med card shows a `📄 Фото рецепта`
  button (`medication_file` → `on_medication_file` sends the original via `FSInputFile`) — a
  manually-entered med has none. The orphan purge (`history.cleanup_orphans`) **skips a file any
  `Medication.source_file` still references** (an auto-routed prescription shares its file with the
  DISCARDED lab report it came from). **💊 з фото рецепта** (`labs/prescription.py`
  extractor + `bot/prescription_flow.py`, the 📷 button): a prescription photo/PDF is OCR'd to
  drug · dose · times (claude, defensive parser, like lab extraction), shown for confirmation
  (rail #5; nothing persists until confirm, rail #2), then a `Medication`+reminders per timed drug.
  The router is registered BEFORE `lab_flow`, state-filtered to `PrescriptionStates.waiting_photo`,
  so an EXPLICIT prescription upload (📷 button) routes here. **Auto-routing of a freely-dropped
  photo** (no button first): the lab read now also CLASSIFIES the upload — the extraction JSON
  carries `document_type` ("lab" | "prescription"), parsed onto `ExtractedReport.document_type` /
  `is_prescription` (merge propagates it across PDF chunks). In `lab_flow._handle_upload`, when the
  read says `is_prescription` AND there are **no** analyte rows (a lab that merely prints a meds
  footer keeps its rows → stays a lab — the conservative guard against hijacking real labs), the
  pending lab report is DISCARDED and the file (already on disk) is handed to
  `prescription_flow.present_prescription_from_path`, which re-reads it with the focused
  prescription parser and confirms (rail #2/#5). So analyses stay a single read; only a detected
  prescription pays a second, dedicated read — the common path is unchanged. A prescription the
  classifier misses (→ "lab") just falls back to today's behaviour (📷 button still works). The
  **dose is stored** on `Medication.dose` as record-keeping (rail #1 permits it) and shown in the
  confirm, but NEVER in a reminder; a med whose time the page didn't print is listed for manual
  entry, never guessed. The daily check-in no longer appears as a deletable reminder — it's an
  info line ("керую цим я") above the list (`_reminders_payload`). On lab confirm the bot **offers** a repeat-lab
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
  deterministic trend engine (chart when ≥2 points). **Dynamics browser** (`/dynamics` + a
  `📈 Динаміка по категоріях` button on the `/history` list): browse indicators grouped by clinical
  CONTEXT across ALL labs — Кров/Сеча/Біохімія/Гормони/Інше/Описові(МРТ/УЗД) — then drill into one
  indicator's trend. `companion/grouping.py` (pure) `categorize(section, analyte)` decides the
  category from the printed panel then an analyte-name fallback (a section-less single-analyte ДІЛА
  row still lands in Біохімія); `history.aggregate_indicators` rolls every analyte across confirmed
  tabular reports into `IndicatorItem`s (category · has_trend · last_flagged), `category_counts`/
  `indicators_in`/`list_narratives` feed the master-detail browser (edit-in-place, paginated; tap an
  indicator → `trend_for_analyte`; the Описові category lists narrative docs). **Charts read the same
  way everywhere** (`charts.render_trend_chart`): green/red in-range-vs-out zones + green ●/red ✕
  status markers (out-of-range labelled), a legend, y-axis always spanning the reference bounds.
  **All retrieval is no-LLM** — listing,
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
  **Navigation cancels a half-open dialog**: the menu's INLINE section-OPENERS (`cb_open_*`/
  `cb_*_list`/`cb_goals_list`, the ones that show a screen, NOT the dialog-STARTERS that set their own
  state) `await state.clear()` first — so tapping ➕ Додати ліки then 🔔 Нагадування no longer leaves
  `MedStates.waiting_name` armed (the bug where a later **proactive check-in reply** got eaten by a
  stale add-medication dialog and a symptom report was stored as a "drug name", bypassing triage).
  **Single-field NAME dialogs screen their input through the gate**: `on_medication_name` and the
  problem `_add_problem` now run `safety.screen(name)` — a symptom / red flag short-circuits to triage
  (surfaced verbatim, nothing stored), exactly like `goals.set_goal` already did. So a symptom typed
  where a name is expected can NEVER be silently saved as a med/concern; it always reaches triage.
  **Belt-and-braces — the check-in itself resets the dialog on fire**: a `DialogReset` callback
  (`app.make_dialog_reset` → clears FSM state+data for the owner's `StorageKey`, since a private chat
  keys `chat_id == user_id == telegram_id`) is threaded into `ReminderScheduler`; `_fire_reminder`
  calls it (best-effort) BEFORE sending a `TYPE_CHECKIN` prompt — so even if a stale dialog is armed
  and the user never navigated away, the check-in reply reaches the gate/companion, not the dialog.
  Only the check-in resets (a medication reminder does not); `dialog_reset=None` in dry-run/tests.
- **Tier 1.3 — button menu** (`bot/menu_flow.py`, `bot/keyboards.py`): a **UI/entry layer only**, no
  new domain logic. The persistent `ReplyKeyboardMarkup` is now **~5 agent-driven sections**
  (the menu→AI-agent overhaul): **🩺 Моє здоровʼя** (a hub → 📊 Аналізи · ⚕️ Проблеми · 🎯 Мої цілі ·
  📝 Чек-ін). **🎯 Мої цілі is its OWN hub button** (`callbacks.MENU_OPEN_GOALS` →
  `companion_flow.open_goals_screen`) — a full goals screen (`_goals_master`): it SUGGESTS goals from
  your problems (`propose_goals` — "Привести X до норми" per out-of-range finding + generic wellness;
  the menu-level de-dup is only that the suggestions live in the goals screen, NOT on ⚕️ Проблеми),
  lists your adopted goals, and offers a `🗄 Закриті цілі — N` archive (`goals.list_closed_goals` =
  ACHIEVED/ABANDONED, `_goals_archive`) where `[↩️ <subject>]` → `goals.reactivate_goal` (→ ACTIVE +
  check-in reconcile) RESTORES a closed goal. (History: goals were briefly FOLDED into ⚕️ Проблеми as
  a `🎯 Мої цілі` sub-button, then split back out to their own hub button per the owner.) ⚕️ Проблеми
  is laid out as **visual GROUPS, not a flat pile** (`_problems_top`):
  out-of-range categories two-per-row, then 📈 на межі, then the management pair `[✅ Під наглядом]
  [🙈 Відкладені]` on one row, then a separated `🎯 Мої цілі` row, then ➕; a legend header
  (`PROBLEM_GROUP_HEADER`) labels the ⚕️-problems vs 🎯-goals split. "Вже відстежую"→"Під наглядом",
  "Приховані"→"Відкладені" (clearer; the dismissed header explains they are findings you ✖-ed)) ·
  **💊 Ліки й нагадування** (a hub → meds list/add + 🔔 Нагадування) · 💰 Ціни / НСЗУ ·
  🧠 Памʼять · ❓ Довідка — shown from `/start`. The two hubs post a section message whose inline
  buttons delegate to the SAME leaf helpers as before (`MENU_OPEN_ANALYSES`/`MENU_PROB_LIST`/
  `MENU_OPEN_GOALS`/`MENU_OPEN_CHECKIN`/`MENU_OPEN_REMINDERS`); the old single-purpose labels
  (`MENU_LABS`/`GOALS`/`PROBLEMS`/`MEDS`/`REMINDERS`/`CHECKIN`) are kept as constants + handlers so a
  **cached old keyboard still works**, and stay in `locale.MENU_LABELS` (current ∪ legacy) so either
  resets a dialog. `start_checkin_dialog(..., telegram_id=)` is threaded on the callback path (a
  callback message's `from_user` is the bot, so the grounded check-in needs the owner's id passed
  explicitly). The native "/" command
  menu is populated on startup (`app.apply_bot_commands` from `locale.BOT_COMMANDS`, `set_my_commands`)
  so **no command must be typed from memory** — a parity test (`tests/test_bot_commands.py`) fails if any
  `Command(...)` handler lacks a "/" menu entry. **❓ Довідка is agent-framed + actionable** (not a
  wall of "/" commands): `HELP_TEXT` explains the paradigm (send photos · just chat · tap a section)
  and points to the native "/" menu; `menu_help` attaches `keyboards.help_keyboard()` — inline
  quick-jumps straight into the agent screens (reusing the existing leaf callbacks; `MENU_OPEN_MEMORY`
  → `cb_open_memory` was added for the 🧠 Памʼять jump). Each label opens a section screen
  (message + inline actions) that **delegates to reused helpers** — the commands are aliases over
  the same `open_*` / `start_*_dialog` helpers (`companion_flow` · `proactive_flow` · `history_flow` ·
  `navigator_flow`). Menu labels are matched by **exact equality** (`F.text == locale.MENU_*`,
  `StateFilter(None)`) in the `menu` router registered **before** history-NL/companion, so a tap never
  leaks into chat; `locale.MENU_LABELS` is also a reset trigger in `CommandStateResetMiddleware`
  (message-level only — callbacks keep their own cancel) so a label tap mid-dialog aborts it. Every
  FSM dialog carries a shared inline `[Скасувати]` (`callbacks.CANCEL_DIALOG`, one central handler →
  clears any state, saves nothing). `/price`·`/coverage` gained a small `NavStates` so the **typed**
  answer routes through `run_price`/`run_coverage` (i.e. `gate.screen`) **identically to the arg** — a
  symptom in the drug field short-circuits to triage. No new models/migrations.
- **Shared persona core** (`dbaylo/persona.py`, root leaf — pure text, NO imports): the parts every
  Дбайло voice must share — `IDENTITY` (the expert "personal health assistant, not a chatbot"),
  `GROUNDING` (profile + current/watch/resolved + state + MEMORY; never invent), `SAFETY_BOUNDARY`
  (the numeric boundary + forbidden phrases + "escalation is NOT yours"), `FORMATTING_LIGHT`.
  Distilled from the consult (the most refined voice) and used to bring the lighter voices UP to it:
  `conversation` and `intake` personas are built from these blocks (so general chat / the interview
  speak as the SAME expert assistant, with identical safety wording); `consult`/`checkin` keep their
  own tuned text. Lives at the package root (not scanned) so it adds no LLM path.
- **Conversation** (`companion/conversation.py`): companion LLM via `llm/client.py`. **A continuous,
  grounded, memory-backed thread** (the unified-chat overhaul), not a stateless one-shot. The persona
  is built from the shared core (expert, not "buddy"): casual chit-chat stays short, a health turn
  switches into expert mode (ground in the data, cautious "може бути повʼязано з…", 1–3 focused
  questions). `generate_reply(text, *, context, history)` lays out the prior turns so it answers the
  LATEST line in thread; with neither history nor context the prompt is the bare text (a single
  turn). Routing/threading live in `bot/companion_flow._run_companion_turn`: the recent
  back-and-forth is kept in **FSM data** (`chat_transcript` + `chat_ts`) under the catch-all
  `StateFilter(None)`, so it threads across free-text turns and is wiped on any `/command` or menu
  tap (the reset middleware clears FSM data too); a gap past `_CHAT_TTL` (6 h) starts a fresh thread.
  `_grounded_context` now also recalls **cross-session memory** (`consult_memory.recall_block`, the
  same store the consult uses) alongside the lab picture + check-in state — so general chat AND the
  intake remember earlier talks; a **substantive** exchange (`_worth_remembering`: not a bare
  greeting/ack) is written back to the GENERAL memory bucket (`report_id=None`). Every reply still
  passes `assert_safe_output` + disclaimer, with a deterministic Ukrainian fallback (a non-`llm`
  reply is never persisted to memory).
- **Smart routing of a data question** (#3, `companion/dataquery.py` pure + `health.list_indicators`
  + `consult_flow.start_data_question_consult`): a free-text turn that reads like a QUESTION
  (`dataquery.is_data_question`) AND names one of the indicators the user actually has data for
  (`dataquery.match_indicator` — stem match tolerant of Ukrainian inflection + a few lay aliases,
  e.g. цукор→глюкоза; over `health.list_indicators`, EVERY analyte not just the trending ones, most-
  interesting first) is routed into a **focused, indicator-grounded consult** about THAT analyte —
  the deep expert answer over its full history + the 🔔/🏥 affordances — instead of the general
  companion. Wired in `on_free_text` **before** the chart-prime (a named OTHER analyte overrides a
  stale prime; a generic "що скажеш?" has no match and falls through to the prime). The turn is
  still gate-screened inside `consult` (escalation unaffected); `dataquery` is pure (no LLM/DB) so
  the choke-point is untouched. `start_primed_consult`/`start_data_question_consult` share
  `_enter_consult`.
- **Precision levers** (#5): four small, low-risk sharpeners of the grounded answer.
  (1) **Humanized trends** — the grounding feeds `trends.direction_phrase(...)` ("moved out of
  range") instead of the raw enum token (`LEFT_RANGE`); still range-relative, never a verdict.
  (2) **Recency pre-computed** — `agerefs.describe_age(date, today)` annotates every grounded date
  with "~3 months ago" so the model reliably nudges re-testing an old flag instead of doing date
  math (in `health.build_health_context` + `consult_context`). (3) **Disclaimer dedup** — the full
  P.S. disclaimer rides only the FIRST turn of a chat/consult thread; continuation turns show
  `locale.DISCLAIMER_SHORT` (`render_*_html(..., full_disclaimer=...)`, flag set from
  `bool(history)`/`bool(transcript)`), so a flowing thread isn't stamped with the whole paragraph
  every message (still not-a-doctor framed; escalations from the gate keep the full text). (4)
  **Config-gated chat model** — `CLAUDE_CHAT_MODEL` (`settings.claude_chat_model`, default empty =
  unchanged) lets the EXPERT chat (companion / consult / intake resolve `model or chat_model or
  None`) use a sharper model than extraction without touching extraction/humanize.
- **Proactive affordances in general chat** (#6): a substantive companion reply carries
  `consult_flow.chat_affordance_keyboard()` — 🔔 Нагадати / 🏥 Де зробити (`CHAT_REMIND`/
  `CHAT_CLINICS`); a bare greeting/ack carries none. Tapping one (or TYPING the request —
  `consult_flow.start_typed_affordance` intercepts `_wants_reminder/booking/clinics` in `on_free_text`
  BEFORE `_run_companion_turn`, so Дбайло ACTS instead of just claiming it will) enters a grounded
  **whole-picture consultation** (`consult_context.KIND_GENERAL`, `_general_context` grounded in
  `health.findings_context` = the indicator picture; profile + memory added by `build_context`) and
  reuses the SAME consult reminder/clinic mini-flows (`_seed_general_consult` seeds `consult_subject`
  + the chat transcript + `ConsultStates.active`). So casual health chat deepens into a real
  consultation only when the user chooses to act; the persona now states it CAN set reminders / find
  clinics (no more "ок, нагадаю" it can't deliver). No new models/migrations; the gate still owns
  escalation (a red flag in the typed request escalates inside the reused flow).
- **Symptom intake** (`companion/intake.py`, Stage 6B): a multi-turn **history-taking** interview —
  when a free-text turn is a symptom (gate→triage) or a broad physical complaint
  (`looks_like_complaint`, router-only), `companion_flow` starts a guided intake (FSM
  `IntakeStates.in_progress`, bounded by `MAX_TURNS`). `intake.advance` re-runs `safety.screen`
  every turn: the **deterministic triage still owns escalation** — a `URGENT_CARE`/`EMERGENCY` red
  flag (or a disordered-eating guardrail signal) **leads** the reply verbatim and the LLM can never
  lower it. Output passes `assert_safe_output` + disclaimer, deterministic fallback. Imports
  `safety.screen` + `run_claude` (gate-routed; never the escalation engines) so the AST choke-point
  stays green. The persona is built from the **shared core** (`dbaylo/persona.py`) and the interview
  is grounded by the same memory-augmented `_grounded_context` as the chat (labs + check-in state +
  cross-session `consult_memory` recall), so it threads on the user's real history. **FSM state is
  persisted** (`bot/storage.py` `SQLiteStorage` — a dedicated SQLite file
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
  built-in HTML fixture (no network). **The bot path enables the LLM fallback** (`run_price(…,
  use_llm_fallback=True)` in `bot/navigator_flow.py`): when the deterministic site parsers miss, a
  guarded Claude **re-parse of the fetched HTML** (`_claude_fallback`, sanity-checked, marked
  «перевір») fills in — so a layout change no longer yields an empty result. The default stays
  `False` (dry-run / tests are deterministic). **💊 Ціна ліків is agent-driven** (`open_price_options`):
  it proposes the owner's OWN medications as one-tap `[💊 <name>]` price buttons (`price_med` by index,
  re-derived; the tap acks first + `keep_typing`, then runs the gated lookup) + `[✏️ Інші ліки]` to
  type another; with no meds it falls back to the type dialog.
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
             labs/ (L2: extraction·prescription·trends·humanize·…)  navigator/ (L4)  llm/ (claude
             subprocess)  db/  web/  locale.py  config.py  persona.py (shared persona core)
             bot/ (handlers · menu_flow · keyboards · *_flow [incl. prescription_flow] · access ·
                   state_reset)  maintenance/
             companion/ (L1 face: goals·checkin·conversation·symptoms · reminders·scheduler·
                         concerns·medications·proactive·callbacks · history·grouping · intake ·
                         consult·consult_context·consult_memory·cities·notecache·notewarm · health)
migrations/  Alembic 0001..0018   tests/  triage·labs.trends·wellness·safety·navigator.guard: highest bar
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
venv/bin/python -m dbaylo.maintenance.normalize_labs --dry-run       # canonicalize stored lab names
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
