#!/usr/bin/env bash
# One-time VPS setup: create the venv, install, migrate, and install + enable the
# systemd unit for the long-polling bot. Run this ONCE on the VPS, from the app dir,
# as the deploy user (it uses sudo for the systemd bits):
#
#     bash deploy/setup-vps.sh
#
# Prerequisites on the VPS: python3.12, git, a filled .env in the app dir.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(whoami)"
cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "Missing $APP_DIR/.env — copy .env.example and fill BOT_TOKEN first." >&2
  exit 1
fi

python3.12 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -e .
venv/bin/alembic upgrade head

echo "Installing systemd unit dbaylo-bot.service ..."
sudo tee /etc/systemd/system/dbaylo-bot.service >/dev/null <<UNIT
[Unit]
Description=Дбайло — personal health companion (Telegram long polling)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/venv/bin/dbaylo-bot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now dbaylo-bot.service
sudo systemctl --no-pager status dbaylo-bot.service | head -n 5

cat <<NOTE

Done. For CI auto-deploy, allow the deploy user to restart without a password:
  echo '${RUN_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart dbaylo-bot.service' \\
    | sudo tee /etc/sudoers.d/dbaylo

The webhook (dbaylo-web) needs HTTPS — set up nginx + certbot for a domain you control,
then create a dbaylo-web.service and set WEBHOOK_BASE_URL in .env. See deploy/README.md.
NOTE
