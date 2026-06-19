# Дбайло — Discovery Document

## Vision
A personal health & wellness companion on Telegram. A trusted friend who wants you
healthier, stronger, and looking better — tracks your labs better than you do, watches
for warning signs, and helps you build sustainable habits.

**Not a doctor. Not a prescriber. A friend with guardrails.**

User-facing positioning: informs & guides (analyzes and tracks labs, flags what's worth
attention) but never prescribes or diagnoses.

## Naming
- **Bot (display name):** Дбайло — coined from "дбати" (to care / look after): "the one who
  cares for you." Warm, distinctly Ukrainian, on-brand with Комунальний Дворецький, and with
  zero medical overclaim (deliberately NOT "Лікар/Доктор").
- **Repo:** `dbaylo` — transliteration, mirroring the `dvoretskyi` (Communal Butler) convention.
- **Domain:** `dbaylo.duckdns.org` — mirroring `dvoretskyi.duckdns.org` on the Hetzner VPS.
- **Telegram handle:** register whatever is free, e.g. `@DbayloBot` / `@dbaylo_bot`.
- Alternatives considered: **Здоровань** (strength/health buddy), **Опора** (reliable support),
  **Пульс** (monitoring feel).

## Core principle
> A caring friend — not a sycophant, not a doctor.

Three commitments drive every design decision:
1. **Honest care over flattery.** The bot tells you the truth when it helps you, even when
   it's not what you want to hear.
2. **Safety asymmetry.** When uncertain, always nudge toward help. Escalate up, never down.
3. **Transparency over false authority.** Where reliable data doesn't exist, say so — never
   fake confidence.

## Language
- **All user-facing text is Ukrainian** — commands, triage messages, disclaimers, errors,
  and (from Stage 2) every LLM-generated reply. Natural, grammatically correct Ukrainian.
- **Code stays English** — identifiers, enum tokens, rule ids, docstrings, comments, CLAUDE.md.
  Standard i18n split: English codebase, Ukrainian presentation layer.
- **Single source of truth:** all Ukrainian strings live in `src/dbaylo/locale.py` so the
  safety guard and tests read from one place — no scattered literals.
- **Safety consequence:** `FORBIDDEN_REASSURANCES` and the forbidden dose/prescription verb
  patterns are written in **Ukrainian** (e.g. "все добре", "можеш не йти до лікаря", dose-directive
  phrasing); safety tests assert against those Ukrainian patterns. An English-only guard would
  scan Ukrainian text and catch nothing.
- **Stage 2 note:** the Claude system prompt instructs "reply exclusively in natural, correct
  Ukrainian"; lab extraction must read Ukrainian forms (Cyrillic, Ukrainian analyte names).

## Architecture — four layers
A friendly wellness layer on top, safety rails underneath. One product, two levels.

### L1 — Wellness companion (the daily face)
- Goals you set: more energy, build strength, clearer skin, better sleep, lean down, etc.
- Lightweight daily check-in: sleep, water, training, mood, symptoms. Logs and trends.
- Personalized nudges drawn from your own data (links to L2).
- Evidence-based only: sleep, hydration, progressive overload, nutrition fundamentals,
  skin/grooming basics — with sources. No bro-science, no miracle supplements.
- Support + accountability: celebrates streaks, notices slips, gently re-engages.
- **Looking better = byproduct of being healthier.** The bot improves health; appearance follows.

### L2 — Lab & data core
- Intake: photo or PDF of lab results.
- Extraction: Claude vision via the `claude` binary (lab forms are tables — Claude reads them
  far more reliably than Tesseract). Extracts analyte, value, unit, reference range, date, lab.
- Structured storage (see Data model).
- **Deterministic trend engine** (not LLM): per-analyte time series → better / worse / in-range,
  with charts. This is the "knows your labs better than you" claim — and for *data* it is 100%
  achievable.
- Claude layer: turns extracted numbers + history into a human summary — what changed, what to
  ask your doctor.
- **OCR confirmation loop:** extracted values are always surfaced for your confirmation; the
  original file/image is always kept and linked. OCR is never trusted silently.

### L3 — Triage (red-flag module)
- Deterministic rule engine, no LLM.
- Maps symptom patterns to escalation: "this is for a doctor" / "urgent care now."
- **Escalate up only.** The bot never autonomously concludes "you can skip the doctor."
  Default nudge is toward care.
- Seeded first for kidney stones (fever/chills, inability to urinate, uncontrolled vomiting,
  first-time blood in urine → escalate).

### L4 — Price & НСЗУ navigator
- Med prices: fetch + Claude-extract from Ukrainian aggregators.
- Ceiling check: МОЗ Національний каталог цін (граничні ціни) — flag pharmacies pricing above
  the legal max.
- Lab/clinic prices: published price lists, compared per service.
- **Coverage first:** check НСЗУ Програма медичних гарантій before searching a price — many
  services are free at contracted facilities.
- Doctor info: aggregated **transparently** — credentials, experience, specialization, price,
  НСЗУ status, location, and reviews *as reviews*. Explicitly labeled "patient opinions, not
  clinical outcomes."

## Safety rails (non-negotiable)
These are encoded in code, not just documented.

1. **Not a doctor, not a prescriber.** Drug references = general info + the official instruction
   (drlz.com.ua, compendium.com.ua) + "confirm with a pharmacist/doctor." Never a dose directive.
2. **OCR never trusted silently.** Always surface extracted values for confirmation; always keep
   the original.
3. **Triage asymmetry.** Escalate up only. Default toward the doctor. No autonomous
   "skip the doctor" decision.
4. **No clinical-outcome claims.** Ukraine has no public outcome data (surgical success,
   complications, mortality by doctor/hospital). Aggregate transparently; never "best surgeon,
   operate here."
5. **Friend, not sycophant.** Honest feedback; pushes back on choices that hurt you.
6. **No crash diets / disordered-eating guardrail.** No precise restrictive calorie targets, no
   facilitation of disordered patterns. Redirect toward sustainable, sourced approaches.
7. **Beauty via health.** Never sells appearance directly.

## Data model (SQLAlchemy 2.0)
- **User** — single-user to start.
- **LabReport** — date, lab, source_file (path), raw_ocr.
- **LabResult** — report_id, analyte, value, unit, ref_low, ref_high, flag.
- **Medication** — name, dose, schedule, prescribed_by (you enter what a doctor prescribed).
- **Condition** — name, notes.
- **Reminder** — type, schedule, payload.
- **CheckIn** — date, sleep, water, mood, symptoms, training.
- **Goal** — type, target, status.

Trends are computed from the LabResult series at query time — never stored as LLM output.

## Tech stack (locked)
- Python 3.12
- aiogram 3 · FastAPI
- SQLAlchemy 2.0 + Alembic · SQLite
- APScheduler (reminders, daily check-in, repeat-lab schedule)
- Claude vision via `claude` binary subprocess (Claude Code OAuth — **not** the Anthropic SDK)
- Pillow only if image prep is needed
- Lean deps, English-only code
- Deploy: Hetzner VPS, `dbaylo.duckdns.org`, webhook pattern (same as Communal Butler)

## Verified data sources
- **Med prices:** Tabletki.ua, Apteki.ua, Doc.ua/apteka, mypharmacy.com.ua
- **Price ceiling:** МОЗ Національний каталог цін
- **Lab prices:** Synevo, Dila, ЕСЛ (published)
- **Coverage:** НСЗУ Програма медичних гарантій
- **Drug instructions:** drlz.com.ua, compendium.com.ua
- **Doctor info (reviews, not outcomes):** Doc.ua and similar

## Non-goals (for now)
- Prescribing or dosing directives
- Any autonomous "you can skip the doctor" conclusion
- Clinical-outcome / "best doctor" ranking
- Diagnosis
- Productization / SaaS (different regulatory tier — personal use only for now)

## Roadmap
- **Stage 1 — Foundation:** repo scaffold + data layer + deterministic triage core + bot
  skeleton + tests. No LLM, OCR, or prices yet.
- **Stage 2 — Lab core:** intake, Claude extraction, OCR-confirm loop, trend engine + charts.
- **Stage 3 — Companion:** goals, daily check-in, reminders, evidence-based nudges, accountability.
- **Stage 4 — Navigator:** med/lab/clinic prices, ceiling check, НСЗУ coverage, transparent
  doctor aggregation.

## Open questions (for the Discovery pass)
- Which conditions to seed red-flag rules for after kidney stones?
- Reminder timezone — Europe/Kyiv assumed.
- Original-file storage — local path vs VPS volume?
- Auth — single-user assumed; revisit if shared later.
