# Дбайло

A personal health & wellness companion on Telegram — coined from *дбати*, "to care."
A trusted friend who tracks your labs better than you do, watches for warning signs, and
helps you build sustainable habits.

> **Your personal health companion — reads and tracks your lab results, flags what's worth
> attention, and helps you build sustainable habits. It informs and guides; it doesn't prescribe
> or replace your doctor.**

Single-user, personal use only. See [`docs/dbaylo-discovery.md`](docs/dbaylo-discovery.md)
for the full vision and [`CLAUDE.md`](CLAUDE.md) for the architecture and safety rails.

## Architecture (four layers)

A friendly wellness face on top, deterministic safety rails underneath.

| Layer | What | Status |
|-------|------|--------|
| **L1** Wellness companion (`companion/`, `wellness/`) | Goals, daily check-in, reminders, companion chat + the wellness guardrail | **Stage 3** ✅ |
| **L2** Lab & data core (`labs/`, `db/`) | Lab intake, extraction, deterministic trends, charts | **Stage 2** ✅ |
| **L3** Triage (`triage/`) | Deterministic red-flag engine — **the safety core** | **Stage 1** ✅ |
| **L4** Price & НСЗУ navigator (`navigator/`) | On-demand prices, МОЗ ceiling, НСЗУ coverage, transparent providers | **Stage 4** ✅ |

Two deterministic cores never call an LLM and only ever **escalate toward care**: **triage**
(symptoms) and the **wellness guardrail** (disordered-eating / unsafe goals). Neither has a code
path that concludes "you can skip the doctor," and the companion LLM never decides escalation.
Safety rails are enforced in code and tests, not just documented (see
`tests/triage/test_safety.py` and `tests/wellness/`).

## Stack

Python 3.12 · aiogram 3 · FastAPI · SQLAlchemy 2.0 + Alembic · SQLite · APScheduler.
Lean dependencies; config is hand-rolled with python-dotenv. Any future Claude calls go
through the `claude` binary via subprocess (Claude Code OAuth), not the Anthropic SDK.

**Language:** the bot speaks Ukrainian to the user; the code stays English. Every
user-facing string lives in `src/dbaylo/locale.py` (including the Ukrainian safety
vocabulary the guard checks against).

## Setup

```bash
# Python 3.12 (this repo was built on 3.12.3)
python3.12 -m venv venv
venv/bin/pip install -e ".[dev]"

cp .env.example .env        # fill in BOT_TOKEN to actually run the bot
venv/bin/alembic upgrade head
```

## Develop

```bash
venv/bin/python -m pytest --cov   # tests + coverage (triage gate >= 90%)
venv/bin/ruff check src tests     # lint
venv/bin/mypy                     # strict type check
venv/bin/dbaylo-web               # FastAPI: GET /health, POST /webhook/{token}
venv/bin/dbaylo-bot               # bot via long polling (needs BOT_TOKEN)
venv/bin/dbaylo-scheduler --dry-run                            # list reminder jobs (fire nothing)
venv/bin/python -m dbaylo.companion.checkin --dry-run          # print the check-in prompt
venv/bin/python -m dbaylo.labs.pipeline --dry-run lab.jpg      # extract only (no DB/Telegram)
venv/bin/python -m dbaylo.navigator.pipeline --dry-run парацетамол   # price a drug from a fixture
```

## Status

**Stage 1 — Foundation ✅** repo scaffold, SQLAlchemy data model + Alembic init migration, the
deterministic triage engine (kidney-stone red flags), an aiogram bot skeleton, and a FastAPI app
with `/health` + webhook.

**Stage 2 — Lab core ✅** send a lab photo/PDF → Claude extraction (via the `claude` binary) →
extracted values shown for confirmation in Ukrainian (date & lab editable) → on confirm,
`LabResult` rows persist → deterministic per-analyte trends → chart + Ukrainian summary. The
trend engine is pure and LLM-free; the humanized summary passes the safety guard with a
deterministic fallback. Original files are always kept; nothing is stored before confirmation.

**Stage 3 — Companion ✅** `/goal` (validated by the wellness guardrail before it's accepted —
an aggressive target is redirected, not stored), `/goals`, a real `/checkin` (sleep/water/mood/
training; symptoms route to the deterministic triage engine, not the LLM), reminders on
APScheduler rebuilt from `Reminder` rows on startup (medication, daily check-in, repeat-lab), and
natural-Ukrainian companion chat. A second deterministic safety core (`wellness/`) handles
disordered-eating / unsafe-goal escalation; every companion reply passes the safety guard
(re-anchored dose detection + restrictive-diet rejection) with a deterministic fallback.
**Stage 3.5** folded the canonical escalation order into a single `safety.gate` choke-point that
every user-text path (and Stage 4) must pass — enforced by an import-graph test.

**Stage 4 — Price & НСЗУ navigator ✅** `/price <drug>` returns cheapest options for an explicitly
named medicine (named-drug only — it never picks a drug for a symptom) and flags a price above the
МОЗ regulated ceiling (or says "no regulated ceiling"); `/coverage <service>` checks НСЗУ ПМГ
**before** price and surfaces "may be free — verify" (never a categorical "free"). Provider
aggregation is transparent — attributes and reviews *as reviews*, always carrying "це думки
пацієнтів, не результати лікування", never a "best surgeon" ranking. Fetches fail soft (a dead
source is skipped and named, never crashes or fabricates a price); sources verified robots-hostile
(tabletki.ua / apteki.ua) are declared-disabled. All free-text routes through `safety.gate` first.

All four roadmap layers are now shipped.
