"""L2 — Lab & data core: intake, extraction, deterministic trends, charts, humanize.

The deterministic engine (:mod:`dbaylo.labs.trends`, :mod:`dbaylo.labs.charts`) and
the LLM layer (:mod:`dbaylo.labs.extraction`, :mod:`dbaylo.labs.humanize`) are kept
strictly separate: trends are computed in code, the model only describes numbers it
is given. A test (`tests/labs/test_no_llm_in_trends.py`) enforces that separation.
"""
