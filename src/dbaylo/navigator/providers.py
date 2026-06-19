"""Transparent provider aggregation (rail #4) — attributes, never a verdict.

We list providers with transparent attributes (specialization, location, НСЗУ-
contract status, reviews *as reviews*) and let the user choose. We never rank a
"best surgeon", never say "operate here", and never present reviews as authority.

The "Це думки пацієнтів, а не результати лікування" label is attached
**deterministically** by :func:`render_providers` — it is part of the template, not
something the LLM is asked to append (correction #3). The rendered text is then run
through the navigator guard (reject superlatives) and ``assert_provider_labeled``
(the last net), so a block that somehow carried a superlative recommendation fails
closed rather than reaching the user.
"""

from __future__ import annotations

from dbaylo import locale
from dbaylo.navigator.guard import assert_provider_labeled, assert_safe_navigator_output
from dbaylo.navigator.types import ProviderInfo


def _attributes(provider: ProviderInfo) -> str:
    parts = [provider.name]
    if provider.specialization:
        parts.append(provider.specialization)
    if provider.location:
        parts.append(provider.location)
    if provider.nszu_contract is not None:
        parts.append(
            locale.NAV_PROVIDER_NSZU_YES if provider.nszu_contract else locale.NAV_PROVIDER_NSZU_NO
        )
    return ", ".join(parts)


def render_providers(providers: list[ProviderInfo]) -> str:
    """Render providers as transparent options, with the mandatory label baked in."""
    lines = [locale.NAV_PROVIDER_HEADER]
    for provider in providers:
        lines.append(f"• {_attributes(provider)}")
        if provider.review_note:
            lines.append(f"  Відгук пацієнта: «{provider.review_note}»")
    lines.append("")
    lines.append(locale.REVIEWS_NOT_OUTCOMES)  # deterministic; always present
    text = "\n".join(lines)
    # Guard order: reject superlatives, then confirm the label is present (last net).
    return assert_provider_labeled(assert_safe_navigator_output(text))
