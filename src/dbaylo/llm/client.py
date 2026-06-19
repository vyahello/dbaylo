"""Thin async wrapper around the `claude` binary (Claude Code OAuth).

Builds the argv, runs the subprocess off the event loop, and returns a small
result object. It does *not* know about labs, prompts, or schemas — callers
(extraction, humanize) own those. Designed to be injected/faked in tests so no
test ever spawns a subprocess or hits the network.

Invocation shape (per the installed CLI, verified at build time):

    claude -p --output-format json --model <m> --append-system-prompt <persona>
           [--allowedTools Read --add-dir <dir>] <prompt>

Note: ``--json-schema`` is intentionally not used — output is constrained by the
prompt and validated by the defensive parser in the caller.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass

from dbaylo.config import get_settings


class ClaudeUnavailable(RuntimeError):
    """Raised when the `claude` binary cannot be launched at all."""


@dataclass(frozen=True)
class ClaudeResult:
    """The outcome of one `claude` invocation.

    ``text`` is the model's answer (the envelope's ``result`` field when
    ``--output-format json`` parsed cleanly, otherwise raw stdout). ``ok`` is
    False on a non-zero exit, a timeout, or an error envelope.
    """

    ok: bool
    text: str
    raw_stdout: str
    exit_code: int | None
    error: str | None = None


async def run_claude(
    prompt: str,
    *,
    append_system_prompt: str,
    model: str | None = None,
    allowed_tools: Sequence[str] = (),
    add_dirs: Sequence[str] = (),
    cwd: str | None = None,
    timeout_s: int | None = None,
) -> ClaudeResult:
    """Run one non-interactive `claude` call and return its result.

    Never raises on model/parse problems — those surface as ``ok=False`` so
    callers stay defensive. Only a failure to *launch* the binary raises
    :class:`ClaudeUnavailable`.
    """
    settings = get_settings()
    model = model or settings.claude_model
    timeout_s = timeout_s or settings.claude_timeout_s

    argv: list[str] = [
        settings.claude_bin,
        "--print",
        "--output-format",
        "json",
        "--model",
        model,
        "--append-system-prompt",
        append_system_prompt,
    ]
    if allowed_tools:
        argv += ["--allowedTools", ",".join(allowed_tools)]
    for directory in add_dirs:
        argv += ["--add-dir", directory]
    argv.append(prompt)

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=os.environ.copy(),
        )
    except (FileNotFoundError, OSError) as exc:  # binary missing / not executable
        raise ClaudeUnavailable(f"could not launch {settings.claude_bin!r}: {exc}") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError:
        process.kill()
        await process.wait()
        return ClaudeResult(ok=False, text="", raw_stdout="", exit_code=None, error="timeout")

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if process.returncode != 0:
        return ClaudeResult(
            ok=False,
            text="",
            raw_stdout=stdout,
            exit_code=process.returncode,
            error=stderr.strip() or f"exit {process.returncode}",
        )

    return _parse_envelope(stdout, process.returncode)


def _parse_envelope(stdout: str, exit_code: int | None) -> ClaudeResult:
    """Extract the model answer from the `--output-format json` envelope."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        # No envelope — hand back raw stdout so the caller can still try to parse.
        return ClaudeResult(ok=True, text=stdout, raw_stdout=stdout, exit_code=exit_code)

    is_error = bool(envelope.get("is_error")) or envelope.get("subtype") not in (None, "success")
    text = envelope.get("result", "")
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    return ClaudeResult(
        ok=not is_error,
        text=text,
        raw_stdout=stdout,
        exit_code=exit_code,
        error=None if not is_error else "error envelope",
    )
