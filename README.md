<p align="center">
  <img src="docs/assets/dbaylo-avatar.png" alt="Дбайло" width="160" style="border-radius:50%">
</p>

<h1 align="center">Дбайло</h1>

<p align="center">
  A personal health &amp; wellness companion on Telegram — coined from <em>дбати</em>, “to care.”
</p>

<p align="center">
  <a href="https://github.com/vyahello/dbaylo/actions/workflows/ci-cd.yml"><img src="https://github.com/vyahello/dbaylo/actions/workflows/ci-cd.yml/badge.svg" alt="CI/CD"></a>
  <img src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=white" alt="Ruff"></a>
  <img src="https://img.shields.io/badge/types-mypy%20--strict-2A6DB2" alt="mypy strict">
  <img src="https://img.shields.io/badge/coverage-%E2%89%A590%25-success" alt="coverage ≥90%">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

> **Your personal health companion — reads and tracks your lab results, flags what's worth
> attention, and helps you build sustainable habits. It informs and guides; it doesn't prescribe
> or replace your doctor.**

Single-user, personal use. The bot speaks **Ukrainian** to you; the code stays English. See
[`docs/dbaylo-discovery.md`](docs/dbaylo-discovery.md) for the full vision and
[`CLAUDE.md`](CLAUDE.md) for the architecture and safety rails.

## What it does

- 🧪 **Reads your labs.** Send a photo or PDF — tabular panels *or* narrative/imaging reports
  (МРТ / УЗД / висновок) — it extracts the values, you confirm them, and it stores them.
- 🔬 **Explains them like a friend.** An expert plain-language reading of each report (grouped by
  body system, what's worth attention and how soon to see which doctor), then **deterministic**
  per-analyte trends with norm-aware charts — never a diagnosis, never a prescription.
- 📈 **Shows the dynamics.** Browse your indicators grouped by context (blood / urine / biochemistry /
  hormones) across **all** your labs, and watch each one move over time.
- 🚑 **Watches for red flags.** A pure rule engine maps symptoms to "see a doctor / urgent / emergency"
  and only ever **escalates toward care**.
- 🌿 **Builds habits.** Goals, a gentle daily check-in (sleep / water / mood / training), medication &
  repeat-lab reminders — supportive, never guilt-trips.
- 💸 **Finds prices & free care.** Looks up the price of a *named* medicine, flags prices above the
  state ceiling, and checks whether a service **may be free under НСЗУ ПМГ** before you pay.

## How it works

A friendly wellness face on top, deterministic safety rails underneath. **Every** piece of user
text passes one choke-point — `safety.gate` — before any LLM or network call:

```
Telegram ─▶ aiogram bot ─▶ safety.gate.screen(text)        ← the only path to the LLM
                                │
             ┌──────────────────┼───────────────────────┐
         symptom?          disordered /              cleared
             │             unsafe goal?                 │
             ▼                  ▼                       ▼
       triage engine      wellness guardrail       LLM (companion ·
      (deterministic,     (deterministic,          lab summary · navigator)
       escalate up)        redirect / support)     every reply: guard + disclaimer + fallback

Labs   photo/PDF ─▶ claude extract ─▶ you confirm ─▶ SQLite ─▶ deterministic trends ─▶ charts + expert reading
Prices /price · /coverage ─▶ gate ─▶ fail-soft fetch ─▶ ceiling & ПМГ-coverage guards (never fabricates)
```

The two deterministic cores (**triage**, **wellness guardrail**) never call an LLM and own *all*
escalation; the LLM never decides it. Safety rails live in code and tests, not just docs.

| Layer | What | Where |
|-------|------|-------|
| **L1** Wellness companion | Goals, daily check-in, reminders, chat + the wellness guardrail | `companion/`, `wellness/` |
| **L2** Lab & data core | Lab intake (tabular + narrative), Claude extraction, expert reading, deterministic trends & charts | `labs/`, `db/` |
| **L3** Triage | Deterministic red-flag engine — **the safety core** | `triage/` |
| **L4** Price & НСЗУ navigator | On-demand prices, МОЗ ceiling, НСЗУ coverage, transparent providers | `navigator/` |
| — | The single user-text → LLM choke-point | `safety/` |

## Quick start

```bash
python3.12 -m venv venv
venv/bin/pip install -e ".[dev]"

cp .env.example .env          # fill BOT_TOKEN (from @BotFather); the rest has sane defaults
venv/bin/alembic upgrade head # create the SQLite schema

venv/bin/dbaylo-bot           # run the bot via long polling (no public URL / cert needed)
```

> **Claude calls** (lab extraction, the Ukrainian summary, the navigator fallback) go through the
> `claude` binary via subprocess (Claude Code OAuth) — install it and sign in once; the bot runs
> fine without it (those features degrade to a safe deterministic fallback).

## Using it

In Telegram:

There's a button menu and a populated "/" command list, so nothing must be typed from memory.

| Command | Does |
|---|---|
| `/start`, `/help` | intro & the command list |
| *(send a photo/PDF)* | read a lab — tabular *or* narrative/imaging — → confirm → expert reading + trends |
| `/history`, `/reports` | browse saved reports (analysis, results, charts, file, delete) — filter by lab/date/`останній` |
| `/dynamics` | indicators grouped by context (blood / urine / biochem …), drill into each trend |
| `/trend <analyte>` | one analyte's movement over time + a chart (deterministic, range-relative) |
| `/checkin` | quick daily check-in (sleep / water / mood / training; symptoms route to triage) |
| `/goal`, `/goals` | set / list a wellness goal (an aggressive target is gently redirected) |
| `/problem`, `/problems` | track something that worries you (daily check-ins while it's active) |
| `/medication`, `/reminders` | medication reminders; list / turn off any reminder |
| `/price <drug>` | cheapest options for a **named** medicine + a state-ceiling check |
| `/coverage <service>` | is it free under НСЗУ ПМГ? (checked *before* price) |

## Develop

```bash
venv/bin/python -m pytest --cov   # tests + coverage (≥90% gate on the deterministic safety surfaces)
venv/bin/ruff check src tests     # lint        venv/bin/ruff format src tests
venv/bin/mypy                     # strict type check
venv/bin/dbaylo-web               # FastAPI: GET /health, POST /webhook/{token}
venv/bin/dbaylo-scheduler --dry-run                            # list reminder jobs (fire nothing)
venv/bin/python -m dbaylo.companion.checkin --dry-run          # print the check-in prompt
venv/bin/python -m dbaylo.labs.pipeline --dry-run lab.jpg      # extract a lab file only (no DB/Telegram)
venv/bin/python -m dbaylo.navigator.pipeline --dry-run парацетамол   # price a drug from a fixture
```

**Stack:** Python 3.12 · aiogram 3 · FastAPI · SQLAlchemy 2.0 + Alembic · SQLite · APScheduler ·
httpx. Lean deps; config is hand-rolled with python-dotenv. Every user-facing string lives in
`src/dbaylo/locale.py` (including the Ukrainian safety vocabulary the guards check against).

## Deploy

Push to `main` → CI runs `ruff` + `mypy` + `pytest`, and on green it **SSHes into the VPS, pulls
`main`, and restarts the `dbaylo-bot` systemd service**. See [`deploy/README.md`](deploy/README.md)
for the one-time VPS setup, the required GitHub secrets, and the optional webhook + TLS
(Let's Encrypt) path for your own domain.

## License

MIT.
