"""Structural invariant: the safety gate is the only path from user text to the LLM.

Import-graph style (like ``tests/labs/test_no_llm_in_trends.py``). Three guards over
the ``bot/``, ``companion/``, and ``navigator/`` packages:

1. **LLM reachability => gate.** Any module importing the LLM client must also
   import ``dbaylo.safety``. A future handler that wires the LLM without the gate
   fails here.
2. **Ordering centralized.** No module (outside the gate) imports the escalation
   entry points directly — ``triage.evaluate``, ``wellness.evaluate``,
   ``symptoms.triage_for_text``. ``detect_symptoms`` stays allowed (it records the
   check-in symptom column, it does not escalate).
3. **Gate purity.** ``safety/gate.py`` imports no LLM / DB / network.
"""

from __future__ import annotations

import ast
from pathlib import Path

import dbaylo

_ROOT = Path(dbaylo.__file__).parent
_SCAN_DIRS = (_ROOT / "bot", _ROOT / "companion", _ROOT / "navigator")
_GATE = _ROOT / "safety" / "gate.py"


def _scanned_files() -> list[Path]:
    return sorted(p for d in _SCAN_DIRS for p in d.rglob("*.py"))


def _imports(path: Path) -> tuple[list[tuple[str, str]], set[str]]:
    """Return ``from``-imports as (module, name) pairs and bare imported modules."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            pairs += [(node.module, alias.name) for alias in node.names]
        elif isinstance(node, ast.Import):
            bare.update(alias.name for alias in node.names)
    return pairs, bare


def _reaches_llm(pairs: list[tuple[str, str]], bare: set[str]) -> bool:
    return (
        any(mod.startswith("dbaylo.llm") for mod, _ in pairs)
        or any(name == "run_claude" for _, name in pairs)
        or any(mod.startswith("dbaylo.llm") for mod in bare)
    )


def _imports_gate(pairs: list[tuple[str, str]], bare: set[str]) -> bool:
    return any(mod.startswith("dbaylo.safety") for mod, _ in pairs) or any(
        mod.startswith("dbaylo.safety") for mod in bare
    )


# Escalation entry points that must only be reached via the gate.
_FORBIDDEN_IMPORTS = {
    ("dbaylo.triage", "evaluate"),
    ("dbaylo.triage.engine", "evaluate"),
    ("dbaylo.wellness", "evaluate"),
    ("dbaylo.wellness.guardrail", "evaluate"),
    ("dbaylo.companion.symptoms", "triage_for_text"),
}


def test_llm_reachability_requires_the_gate() -> None:
    for path in _scanned_files():
        pairs, bare = _imports(path)
        if _reaches_llm(pairs, bare):
            assert _imports_gate(pairs, bare), (
                f"{path.relative_to(_ROOT)} reaches the LLM without importing dbaylo.safety"
            )


def test_escalation_ordering_is_centralized_in_the_gate() -> None:
    for path in _scanned_files():
        pairs, _ = _imports(path)
        for mod, name in pairs:
            assert (mod, name) not in _FORBIDDEN_IMPORTS, (
                f"{path.relative_to(_ROOT)} imports the escalation entry point "
                f"{mod}.{name}; route user text through dbaylo.safety.gate.screen instead"
            )


def test_gate_imports_no_llm_db_or_network() -> None:
    pairs, bare = _imports(_GATE)
    modules = [mod for mod, _ in pairs] + list(bare)
    forbidden_substrings = ("llm", "claude", "subprocess", "sqlalchemy", "aiosqlite", "httpx")
    for mod in modules:
        lowered = mod.casefold()
        assert not any(s in lowered for s in forbidden_substrings), f"gate imports {mod}"
        assert "db" not in mod.split("."), f"gate imports a DB module: {mod}"


def test_invariant_is_not_vacuous() -> None:
    """At least one scanned module reaches the LLM (and therefore must use the gate)."""
    conversation = _ROOT / "companion" / "conversation.py"
    pairs, bare = _imports(conversation)
    assert _reaches_llm(pairs, bare) and _imports_gate(pairs, bare)
