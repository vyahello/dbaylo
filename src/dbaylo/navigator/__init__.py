"""L4 — price & НСЗУ navigator.

On-demand med/lab/clinic price lookup, МОЗ price-ceiling checks, НСЗУ coverage
("may be free under ПМГ — verify"), and transparent provider aggregation. No price
DB — everything is fetched on demand and lightly cached.

Every free-text entry point routes through :func:`dbaylo.safety.gate.screen` first
(a symptom short-circuits to triage), and all output passes the navigator guard
(:mod:`dbaylo.navigator.guard`): no superlative provider recommendations (rail #4),
no fabricated "free"/"overpriced" claims, named-drug-only price lookups (rail #1).
"""
