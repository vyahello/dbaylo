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
- **L4 — Price & НСЗУ navigator** — med/lab/clinic prices, price-ceiling checks, coverage
  lookup, transparent doctor info. Not built yet.

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
4. **No clinical-outcome claims / no "best doctor" ranking** (L4, later).
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
  `sonnet`, escalates to `opus`; never `haiku`.
- **Confirmation** (`bot/lab_flow.py`): extracted values (incl. report date & lab — a wrong date
  corrupts the series) are shown in Ukrainian and editable. **Nothing is written to the DB until
  the user confirms** (rail #2); pending values live in FSM state. The original file is always kept.
- **Trend engine** (`labs/trends.py`): pure, deterministic, **no LLM/DB/network import** (enforced
  by a test). Direction is **range-relative** (`RETURNED_TO_RANGE`, `APPROACHING_RANGE`, …), never
  a health verdict (rail #4); IMPROVING/WORSENING `Polarity` is **internal only**. Series are
  grouped by a normalized analyte name + small `ANALYTE_ALIASES` map (known limitation: extend it).
- **Humanize** (`labs/humanize.py`): LLM writes the Ukrainian summary; every output passes
  `assert_safe_output`, with a deterministic Ukrainian fallback. Disclaimer always appended.

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
  **source of truth**; `schedule` is `cron:<expr>` or `date:<iso>`. `build_scheduler` rebuilds
  one job per active row on startup (survives restart), `coalesce`/`misfire_grace_time` set;
  fired one-off reminders are soft-deleted (`Reminder.active`). `next_run` is **not stored**
  (read from the built scheduler). `--dry-run` lists jobs without firing. Medication reminder
  text never carries a dose.
- **Conversation** (`companion/conversation.py`): companion LLM via `llm/client.py`. Every reply
  passes `assert_safe_output` + disclaimer, with a deterministic Ukrainian fallback. The persona
  forbids fabricated sources/statistics and encodes the numeric boundary.
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

## Layout

```
src/dbaylo/  triage/ (L3)  wellness/ (L1 guardrail core)  safety/ (gate: the user-text choke-point)
             labs/ (L2)  companion/ (L1 face: goals·checkin·reminders·scheduler·conversation·symptoms)
             llm/ (claude subprocess)  db/  bot/  web/  locale.py  config.py
migrations/  Alembic 0001..0003   tests/  triage/, labs/trends, wellness/, safety/ carry the highest bar
```

## Dev commands

```bash
venv/bin/python -m pytest --cov   # tests + coverage (gate >= 90% on triage·labs.trends·wellness·safety)
venv/bin/ruff check src tests     # lint        venv/bin/ruff format src tests
venv/bin/mypy                     # strict type check
venv/bin/alembic upgrade head     # apply migrations to the DB
venv/bin/alembic revision --autogenerate -m "msg"   # new migration after model changes
venv/bin/dbaylo-web               # serve FastAPI (/health, /webhook/{token})
venv/bin/dbaylo-bot               # run the bot via long polling (needs BOT_TOKEN)
venv/bin/dbaylo-scheduler --dry-run                         # list reminder jobs, fire nothing
venv/bin/python -m dbaylo.companion.checkin --dry-run       # print the check-in prompt
venv/bin/python -m dbaylo.labs.pipeline --dry-run lab.jpg   # extract only, no DB/Telegram
```

After any model change: regenerate a migration and run `alembic check` (must report no drift).

## Roadmap

Stage 1 (done): skeleton + safety core. Stage 2 (done): lab intake + Claude extraction +
OCR-confirm loop + deterministic trends + charts + humanized summary. Stage 3 (done): goals,
daily check-in, reminders (APScheduler, DB-as-source-of-truth), companion chat, the wellness
guardrail. Stage 4: price & НСЗУ navigator.
