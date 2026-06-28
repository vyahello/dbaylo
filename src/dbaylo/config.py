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
    # Owner lock: only this Telegram user id may use the bot. 0 (the default) means
    # "no owner set" -> the bot refuses everyone (fail-closed), so this MUST be set
    # in production. Personal, single-user medical data — never leave it open.
    owner_telegram_id: int = 0
    # Public base URL for the webhook entrypoint (Stage 1: placeholder).
    webhook_base_url: str = ""
    # Database URL; defaults to a local SQLite file at the repo root.
    database_url: str = f"sqlite:///{ROOT_DIR / 'dbaylo.db'}"
    # Timezone for reminders / check-ins (discovery assumes Europe/Kyiv).
    timezone: str = "Europe/Kyiv"
    # When the daily proactive check-in fires (local time). Reconcile re-times the existing
    # reminder to this on startup, so changing it here + a restart moves the ping.
    checkin_hour: int = 10
    checkin_minute: int = 0
    # Where original lab files are kept (Stage 2).
    storage_dir: Path = ROOT_DIR / "data" / "files"
    # Persistent FSM store (Stage 6): a dedicated SQLite file so in-progress dialogs /
    # symptom interviews survive a restart. Kept separate from the domain DB so Alembic
    # and the backups stay focused on real data.
    fsm_db_path: Path = ROOT_DIR / "data" / "fsm.sqlite"
    # The `claude` binary (Claude Code OAuth) used for lab extraction / humanization.
    claude_bin: str = "claude"
    # Default model alias for extraction; escalates to "opus" on failure (Stage 2).
    claude_model: str = "sonnet"
    # Model for the interactive EXPERT chat (companion / consult / symptom intake). Empty -> use
    # ``claude_model``; set CLAUDE_CHAT_MODEL=opus to trade latency/cost for a sharper reading where
    # precision matters most. Default keeps behaviour unchanged.
    claude_chat_model: str = ""
    # Hard timeout (seconds) for a single `claude` subprocess call (chat / humanize).
    claude_timeout_s: int = 180
    # Extraction reads whole documents by vision — a big multi-page panel (e.g. an
    # 8-page Synevo report with ~80 analytes) legitimately needs much longer than a chat
    # turn, so lab extraction gets its own, larger timeout. With paged extraction this is
    # the per-PAGE ceiling (a single page is fast and stays well under it).
    claude_extract_timeout_s: int = 600
    # A multi-page PDF is split and its pages extracted concurrently. Each `claude` process
    # uses ~470 MB, so this caps how many run at once — default 2 fits a small (~4 GB) VPS;
    # raise it on a bigger box for more parallelism (closer to slowest-single-page latency).
    claude_extract_concurrency: int = 2
    # The expert interpretation (Stage 5) writes a full multi-section reading of every flagged
    # analyte — for a big panel that is far more than a chat turn, so it gets its own, larger
    # timeout (per SECTION). Too small and a section times out and degrades to a deterministic line.
    claude_interpret_timeout_s: int = 600
    # The four interpretation sections (Загалом / Варто звернути увагу / Що допоможе / Коли до
    # лікаря) are generated as concurrent `claude` calls. Each uses ~470 MB, so cap how many run
    # at once — 3 fits a small (~4 GB) VPS (~1.4 GB) and cuts the wait by ~40%; raise it on a
    # bigger box (4 = all sections at once ≈ slowest single section).
    claude_interpret_concurrency: int = 3
    # The price / coverage WebSearch+WebFetch agent. It opens pages to verify in-stock prices, which
    # is slow — but it must NOT hang the chat for minutes. A tighter cap than interpret: on a
    # timeout it falls back to "не вдалося" fast instead of leaving "typing…" for 10 min.
    claude_price_timeout_s: int = 150
    # Webhook server bind. Defaults to localhost — on the VPS, nginx terminates TLS
    # and proxies to it; set WEB_HOST=0.0.0.0 only to expose it directly.
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            bot_token=_get("BOT_TOKEN", ""),
            owner_telegram_id=int(_get("OWNER_TELEGRAM_ID", "0")),
            webhook_base_url=_get("WEBHOOK_BASE_URL", ""),
            database_url=_get("DATABASE_URL", cls.database_url),
            timezone=_get("TZ", "Europe/Kyiv"),
            checkin_hour=int(_get("CHECKIN_HOUR", str(cls.checkin_hour))),
            checkin_minute=int(_get("CHECKIN_MINUTE", str(cls.checkin_minute))),
            storage_dir=Path(_get("STORAGE_DIR", str(cls.storage_dir))),
            fsm_db_path=Path(_get("FSM_DB_PATH", str(cls.fsm_db_path))),
            claude_bin=_get("CLAUDE_BIN", "claude"),
            claude_model=_get("CLAUDE_MODEL", "sonnet"),
            claude_chat_model=_get("CLAUDE_CHAT_MODEL", ""),
            claude_timeout_s=int(_get("CLAUDE_TIMEOUT_S", "180")),
            claude_extract_timeout_s=int(_get("CLAUDE_EXTRACT_TIMEOUT_S", "600")),
            claude_extract_concurrency=int(_get("CLAUDE_EXTRACT_CONCURRENCY", "2")),
            claude_interpret_timeout_s=int(_get("CLAUDE_INTERPRET_TIMEOUT_S", "600")),
            claude_interpret_concurrency=int(_get("CLAUDE_INTERPRET_CONCURRENCY", "3")),
            claude_price_timeout_s=int(_get("CLAUDE_PRICE_TIMEOUT_S", "150")),
            web_host=_get("WEB_HOST", "127.0.0.1"),
            web_port=int(_get("WEB_PORT", "8000")),
        )


def get_settings() -> Settings:
    """Return settings resolved from the current environment."""
    return Settings.from_env()
