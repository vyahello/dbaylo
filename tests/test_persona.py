"""The shared persona core (``dbaylo.persona``) — invariants every voice inherits.

Pure string checks: the blocks are prepended into every voice's prompt, so a regression here would
change how ALL of them speak. Cheap to guard, expensive to lose."""

from __future__ import annotations

from dbaylo import persona


def test_grounding_forbids_slash_commands_and_points_at_buttons() -> None:
    # Regression: the companion used to punt a "have I tested X?" question to "/history" (a raw
    # slash command) because it lacked the full analyte inventory. The shared grounding now (a)
    # forbids naming any command and steers to the on-screen buttons, and (b) tells the model to
    # answer existence from the COMPLETE INDICATOR INVENTORY instead of hedging.
    text = persona.GROUNDING
    assert "slash command" in text
    assert "/history" in text  # named as an example of what NOT to emit
    assert "BUTTONS" in text
    assert "COMPLETE INDICATOR INVENTORY" in text
