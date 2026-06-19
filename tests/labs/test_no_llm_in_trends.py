"""Structural guard: the deterministic trend engine must not touch the LLM layer.

Trends are computed in code, never by the model (discovery L2). This test fails
if anyone wires an LLM import into the engine.
"""

from __future__ import annotations

import ast
from pathlib import Path

import dbaylo.labs.trends as trends

_SOURCE = Path(trends.__file__).read_text(encoding="utf-8")


def _imported_modules() -> set[str]:
    tree = ast.parse(_SOURCE)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_trends_does_not_import_llm_or_humanize() -> None:
    modules = _imported_modules()
    assert not any("llm" in m for m in modules), modules
    assert not any("humanize" in m for m in modules), modules
    assert not any("claude" in m.casefold() for m in modules), modules


def test_trends_source_has_no_claude_reference() -> None:
    assert "run_claude" not in _SOURCE
    assert "subprocess" not in _SOURCE
