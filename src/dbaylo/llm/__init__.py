"""LLM layer — the only place that shells out to the `claude` binary.

Isolated from the deterministic engine on purpose (discovery L2): trends are
computed in code, never by the model. The model is invoked here for lab
extraction and for humanizing already-computed numbers — nothing else.

All calls go through the `claude` binary via subprocess (Claude Code OAuth),
never the Anthropic SDK.
"""

from dbaylo.llm.client import ClaudeResult, ClaudeUnavailable, run_claude

__all__ = ["ClaudeResult", "ClaudeUnavailable", "run_claude"]
