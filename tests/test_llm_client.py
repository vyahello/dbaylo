"""Regression tests for the `claude` argv builder.

The CLI's ``--add-dir`` / ``--allowedTools`` are variadic (``<...>``). The positional
prompt MUST come after a ``--`` terminator, or the trailing variadic option swallows
it and the CLI fails with "Input must be provided ... as a prompt argument" — which
silently broke every lab extraction (the only path passing ``add_dirs``).
"""

from __future__ import annotations

from dbaylo.llm.client import build_argv

_COMMON = {"claude_bin": "claude", "append_system_prompt": "persona", "model": "sonnet"}


def test_prompt_is_terminated_so_it_is_never_swallowed() -> None:
    argv = build_argv("EXTRACT THIS", allowed_tools=["Read"], add_dirs=["/labs"], **_COMMON)
    # The prompt is the final token, immediately preceded by the `--` terminator.
    assert argv[-2:] == ["--", "EXTRACT THIS"]
    # And the `--` comes AFTER the variadic --add-dir value (the bug was the reverse).
    assert argv.index("--add-dir") < argv.index("--")


def test_terminator_present_even_without_tools() -> None:
    # The conversation / humanize path passes no tools; the terminator is harmless and
    # kept for consistency (verified to still return a normal reply).
    argv = build_argv("привіт", **_COMMON)
    assert argv[-2:] == ["--", "привіт"]
    assert "--add-dir" not in argv and "--allowedTools" not in argv


def test_core_flags_present() -> None:
    argv = build_argv("p", **_COMMON)
    assert argv[0] == "claude"
    assert "--print" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--append-system-prompt") + 1] == "persona"


def test_multiple_add_dirs_each_get_their_own_flag() -> None:
    argv = build_argv("p", add_dirs=["/a", "/b"], **_COMMON)
    assert argv.count("--add-dir") == 2
    assert argv[-1] == "p"  # still the trailing positional, after `--`
