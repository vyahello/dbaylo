"""Per-source price adapters. Each declares its robots posture; the orchestrators
only fetch ``ALLOWED`` ones. tabletki.ua / apteki.ua are declared **disabled**
(robots-hostile / query-search disallowed) and are never fetched.
"""

from dbaylo.navigator.sources.base import (
    DISABLED_SOURCES,
    ENABLED_SOURCES,
    HtmlSource,
    RobotsPosture,
)

__all__ = ["DISABLED_SOURCES", "ENABLED_SOURCES", "HtmlSource", "RobotsPosture"]
