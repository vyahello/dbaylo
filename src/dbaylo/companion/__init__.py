"""L1 companion (the daily wellness face): goals, check-in, reminders, chat.

Non-safety logic that *uses* the two deterministic cores. Escalation is never
decided here — symptom routing defers to :mod:`dbaylo.triage` and disordered-
pattern / unsafe-goal routing defers to :mod:`dbaylo.wellness`. Every LLM reply
passes ``triage.safety.assert_safe_output`` with a deterministic fallback.
"""
