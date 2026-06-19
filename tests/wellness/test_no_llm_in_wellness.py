"""Structural guard: the wellness guardrail must stay deterministic.

Like the trend engine, the L1 safety core never calls an LLM and never touches the
DB or the network. This test fails if anyone wires such an import into any wellness
module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import dbaylo.wellness.guardrail as guardrail
import dbaylo.wellness.rules as rules
import dbaylo.wellness.signals as signals
import dbaylo.wellness.types as types

_MODULES = (guardrail, rules, signals, types)
_FORBIDDEN = ("llm", "claude", "subprocess", "sqlalchemy", "aiohttp", "httpx", "socket", "requests")


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_wellness_modules_are_deterministic() -> None:
    for module in _MODULES:
        source = Path(module.__file__).read_text(encoding="utf-8")
        modules = _imported_modules(source)
        for forbidden in _FORBIDDEN:
            assert not any(forbidden in m.casefold() for m in modules), (module.__name__, modules)
        assert "run_claude" not in source, module.__name__
