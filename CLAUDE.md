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
  lightweight check-ins, evidence-based nudges, accountability.
- **L2 — Lab & data core** (`src/dbaylo/db/`) — lab intake, structured storage, and a
  **deterministic** trend engine (never LLM output). Stage 1 ships the schema only.
- **L3 — Triage** (`src/dbaylo/triage/`) — the deterministic red-flag safety core.
  **No LLM.** Pure functions, highest test coverage. This is the most important module.
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
5. **OCR never trusted silently** (L2, later) — always surface for confirmation, always keep
   the original file (`LabReport.source_file`).
6. **Friend, not sycophant; no crash diets; beauty via health** (L1, later).

When in doubt, **escalate toward care.** Never add prescribing logic or any autonomous
"skip the doctor" logic, anywhere.

## Triage model (the core)

- `Symptom` (StrEnum) — controlled vocabulary in. No free text, no LLM in this layer.
- `Action` (IntEnum, ordered) — `MONITOR < SEE_DOCTOR < URGENT_CARE < EMERGENCY`.
  The **ordering is the safety mechanism** (`max` = escalate up).
- `TriageRule` — atomic; fires iff `triggers ⊆ report.symptoms` (AND within a rule;
  use multiple rules for OR). Seeded with kidney-stone red flags in `rules.py`.
- `evaluate(report) -> TriageOutcome` — the only entry point.

## Stack (locked — justify any new dependency)

Python **3.12** · aiogram 3 · FastAPI · SQLAlchemy 2.0 + Alembic · SQLite · APScheduler
(declared, wired in Stage 3). Config is hand-rolled (`config.py` + python-dotenv) — lean
by choice. **Any Claude calls go through the `claude` binary via subprocess (Claude Code
OAuth), NOT the Anthropic SDK.** (No LLM in Stage 1.) Keep a `--dry-run` path for any
external action added later. English-only code and comments.

## Layout

```
src/dbaylo/  triage/ (L3 core)  db/ (models+base)  bot/ (aiogram)  web/ (FastAPI)  config.py
migrations/  Alembic env + versions      tests/  triage/ has the highest bar
```

## Dev commands

```bash
venv/bin/python -m pytest --cov   # tests + coverage (triage gate: >= 90%)
venv/bin/ruff check src tests     # lint        venv/bin/ruff format src tests
venv/bin/mypy                     # strict type check
venv/bin/alembic upgrade head     # apply migrations to the DB
venv/bin/alembic revision --autogenerate -m "msg"   # new migration after model changes
venv/bin/dbaylo-web               # serve FastAPI (/health, /webhook/{token})
venv/bin/dbaylo-bot               # run the bot via long polling (needs BOT_TOKEN)
```

After any model change: regenerate a migration and run `alembic check` (must report no drift).

## Roadmap

Stage 1 (done): skeleton + safety core. Stage 2: lab intake + Claude extraction + trends.
Stage 3: goals, check-ins, reminders, nudges. Stage 4: price & НСЗУ navigator.
