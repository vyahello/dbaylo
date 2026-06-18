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
| **L2** Lab & data core (`db/`) | Lab intake, storage, deterministic trends | schema only (Stage 1) |
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
```

## Status — Stage 1

Skeleton + safety core only. No OCR, LLM, price scraping, or wellness chat yet. Delivered:
the repo scaffold, the SQLAlchemy data model + Alembic init migration, the deterministic
triage engine (kidney-stone red flags, 100% covered), an aiogram bot skeleton
(`/start`, `/help`, stub `/checkin`), and a FastAPI app with `/health` and the webhook entrypoint.
