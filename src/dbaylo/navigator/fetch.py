"""Defensive HTTP fetch for the navigator — fail-soft, on-demand, lightly cached.

Mirrors the ``run_claude`` discipline: a dead source, timeout, or non-200 never
raises and never fabricates — it returns ``FetchResult(ok=False, ...)`` so callers
degrade gracefully (skip the source, record it). On-demand only: a short-TTL on-disk
cache avoids hammering sources, and there is no bulk crawl / price DB.

A descriptive User-Agent identifies the assistant. Each source adapter declares its
robots posture; the orchestrators only fetch ``ALLOWED`` sources.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from dbaylo.config import get_settings

USER_AGENT = "Dbaylo/0.1 (+personal health assistant; on-demand, single-user)"
DEFAULT_TIMEOUT_S = 10.0
CACHE_TTL_S = 6 * 3600


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    url: str
    text: str = ""
    status: int | None = None
    error: str | None = None


# Injected by orchestrators; faked in tests so nothing ever hits the network.
Fetcher = Callable[[str], Awaitable[FetchResult]]


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return get_settings().storage_dir / "nav_cache" / f"{digest}.html"


def _read_cache(path: Path, ttl_s: int) -> str | None:
    try:
        if path.is_file() and (time.time() - path.stat().st_mtime) < ttl_s:
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def _write_cache(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass  # caching is best-effort; never let it break a fetch


async def fetch(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    ttl_s: int = CACHE_TTL_S,
    use_cache: bool = True,
) -> FetchResult:
    """GET ``url`` and return its body; never raises, never fabricates."""
    path = _cache_path(url)
    if use_cache:
        cached = _read_cache(path, ttl_s)
        if cached is not None:
            return FetchResult(ok=True, url=url, text=cached, status=200)

    try:
        async with httpx.AsyncClient(
            timeout=timeout_s, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = await client.get(url)
    except (httpx.HTTPError, OSError) as exc:
        return FetchResult(ok=False, url=url, error=str(exc))

    if response.status_code != 200:
        return FetchResult(ok=False, url=url, status=response.status_code, error="non-200")

    if use_cache:
        _write_cache(path, response.text)
    return FetchResult(ok=True, url=url, text=response.text, status=200)
