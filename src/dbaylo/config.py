"""Application configuration.

Hand-rolled and lean by choice: a frozen dataclass loaded from the environment
(with ``.env`` support via python-dotenv). No settings framework — consistent
with the project's lean-deps stance. If config grows materially in later stages
(source endpoints, claude binary path, file-storage roots), revisit whether a
settings library starts to pay for itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env once, at import time. Real environment variables take precedence.
load_dotenv()

# Repo root: src/dbaylo/config.py -> parents[2].
ROOT_DIR = Path(__file__).resolve().parents[2]


def _get(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings."""

    # Telegram bot token (empty until provisioned; required only to actually run).
    bot_token: str = ""
    # Public base URL for the webhook entrypoint (Stage 1: placeholder).
    webhook_base_url: str = ""
    # Database URL; defaults to a local SQLite file at the repo root.
    database_url: str = f"sqlite:///{ROOT_DIR / 'dbaylo.db'}"
    # Timezone for reminders / check-ins (discovery assumes Europe/Kyiv).
    timezone: str = "Europe/Kyiv"
    # Where original lab files are kept (Stage 2).
    storage_dir: Path = ROOT_DIR / "data" / "files"
    # The `claude` binary (Claude Code OAuth) used for lab extraction / humanization.
    claude_bin: str = "claude"
    # Default model alias for extraction; escalates to "opus" on failure (Stage 2).
    claude_model: str = "sonnet"
    # Hard timeout (seconds) for a single `claude` subprocess call.
    claude_timeout_s: int = 180

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            bot_token=_get("BOT_TOKEN", ""),
            webhook_base_url=_get("WEBHOOK_BASE_URL", ""),
            database_url=_get("DATABASE_URL", cls.database_url),
            timezone=_get("TZ", "Europe/Kyiv"),
            storage_dir=Path(_get("STORAGE_DIR", str(cls.storage_dir))),
            claude_bin=_get("CLAUDE_BIN", "claude"),
            claude_model=_get("CLAUDE_MODEL", "sonnet"),
            claude_timeout_s=int(_get("CLAUDE_TIMEOUT_S", "180")),
        )


def get_settings() -> Settings:
    """Return settings resolved from the current environment."""
    return Settings.from_env()
