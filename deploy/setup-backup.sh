#!/usr/bin/env bash
# One-time: install + enable the nightly off-box backup timer. Run on the VPS:
#   bash deploy/setup-backup.sh
#
# Prereqs (the script checks them):
#   sudo apt install -y rclone sqlite3   (+ age, only if you encrypt)
#   # add to ~/dbaylo/.env:
#   #   BACKUP_RCLONE_REMOTE=~/.dbaylo/backups   (local now; b2:bucket/dbaylo later)
#   #   BACKUP_RETENTION_DAYS=14
#   #   BACKUP_AGE_RECIPIENT=ssh-ed25519 AAAA...         (OPTIONAL: encrypt the archive)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(whoami)"

for tool in rclone sqlite3; do
  command -v "$tool" >/dev/null || { echo "Install '$tool' first (sudo apt install $tool)" >&2; exit 1; }
done
grep -q '^BACKUP_RCLONE_REMOTE=.\+' "$APP_DIR/.env" || { echo "Set BACKUP_RCLONE_REMOTE in $APP_DIR/.env" >&2; exit 1; }
if grep -q '^BACKUP_AGE_RECIPIENT=.\+' "$APP_DIR/.env"; then
  command -v age >/dev/null || { echo "BACKUP_AGE_RECIPIENT is set but 'age' is not installed" >&2; exit 1; }
fi

sudo tee /etc/systemd/system/dbaylo-backup.service >/dev/null <<UNIT
[Unit]
Description=Дбайло off-box backup (encrypted DB + lab files -> rclone)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=/usr/bin/env bash ${APP_DIR}/deploy/backup.sh
UNIT

sudo tee /etc/systemd/system/dbaylo-backup.timer >/dev/null <<UNIT
[Unit]
Description=Nightly Дбайло off-box backup

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now dbaylo-backup.timer
echo "Enabled. Run a test backup now:"
echo "  sudo systemctl start dbaylo-backup.service && journalctl -u dbaylo-backup -n 30 --no-pager"
