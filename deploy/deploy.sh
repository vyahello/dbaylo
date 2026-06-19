#!/usr/bin/env bash
# Run on the VPS by CI *after* it has updated the git checkout (git reset --hard
# origin/main). Installs deps, migrates, and restarts the bot. Idempotent. Requires:
# python3.12 on PATH, a one-time `deploy/setup-vps.sh` run (installs the systemd
# unit) and passwordless sudo for the systemctl restart.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [ ! -d venv ]; then
  python3.12 -m venv venv
fi

venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -e .
venv/bin/alembic upgrade head

# Restart the long-polling bot (works without a TLS cert). Best-effort: the code is
# already synced and migrated above, so a missing unit or missing NOPASSWD sudo is a
# warning, not a failed deploy. `sudo -n` fails fast instead of hanging on a password.
if systemctl list-unit-files 2>/dev/null | grep -q '^dbaylo-bot\.service'; then
  sudo -n systemctl restart dbaylo-bot.service \
    && echo "Restarted dbaylo-bot.service" \
    || echo "::warning::Could not restart dbaylo-bot.service (need NOPASSWD sudo — see deploy/README.md)"
else
  echo "::warning::dbaylo-bot.service not installed yet — run deploy/setup-vps.sh once on the VPS"
fi

echo "Deployed $(git rev-parse --short HEAD 2>/dev/null || echo HEAD) to $APP_DIR"
