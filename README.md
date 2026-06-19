# Дбайло

A personal health & wellness companion on Telegram — coined from *дбати*, "to care."
A trusted friend who tracks your labs better than you do, watches for warning signs, and
helps you build sustainable habits.

> **Not a doctor. Not a prescriber. A friend with guardrails.**

Single-user, personal use only. See [`docs/dbaylo-discovery.md`](docs/dbaylo-discovery.md)
for the full vision and [`CLAUDE.md`](CLAUDE.md) for the architecture and safety rails.

## Architecture (four layers)

A friendly wellness face on top, deterministic safety rails underneath.

| Layer | What | Status |
|-------|------|--------|
| **L1** Wellness companion (`bot/`) | Goals, check-ins, evidence-based nudges | Stage 3 |
| **L2** Lab & data core (`labs/`, `db/`) | Lab intake, extraction, deterministic trends, charts | **Stage 2** ✅ |
| **L3** Triage (`triage/`) | Deterministic red-flag engine — **the safety core** | **Stage 1** ✅ |
| **L4** Price & НСЗУ navigator | Prices, ceiling checks, coverage, doctor info | Stage 4 |

The triage core never calls an LLM and only ever **escalates toward care** — it has no code
path that concludes "you can skip the doctor." Safety rails are enforced in code and tests,
not just documented (see `tests/triage/test_safety.py`).

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
venv/bin/python -m dbaylo.labs.pipeline --dry-run lab.jpg   # extract only (no DB/Telegram)
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

Next: Stage 3 (goals, daily check-in, reminders, nudges) and Stage 4 (price & НСЗУ navigator).
