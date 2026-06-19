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
# already synced and migrated above. Attempt the restart directly with `sudo -n`
# (fails fast, never hangs) — a missing unit or missing NOPASSWD sudo becomes a
# warning, not a failed deploy. We do NOT pre-check with `systemctl list-unit-files`:
# that grep behaves differently under the CI ssh shell and was silently skipping the
# restart even though the unit was installed.
if sudo -n systemctl restart dbaylo-bot.service 2>/dev/null; then
  echo "Restarted dbaylo-bot.service"
else
  echo "::warning::Could not restart dbaylo-bot.service — unit installed (deploy/setup-vps.sh) and NOPASSWD sudo set?"
fi

echo "Deployed $(git rev-parse --short HEAD 2>/dev/null || echo HEAD) to $APP_DIR"
