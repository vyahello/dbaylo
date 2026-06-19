#!/usr/bin/env bash
# One-time: install + enable the nightly off-box backup timer. Run on the VPS:
#   bash deploy/setup-backup.sh
#
# Prereqs (the script checks them):
#   sudo apt install -y rclone age sqlite3
#   rclone config            # add a Backblaze B2 remote, e.g. "b2"
#   # add to ~/dbaylo/.env:
#   #   BACKUP_AGE_RECIPIENT=ssh-ed25519 AAAA...    (your SSH/age PUBLIC key)
#   #   BACKUP_RCLONE_REMOTE=b2:your-bucket/dbaylo
#   #   BACKUP_RETENTION_DAYS=14
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(whoami)"

for tool in rclone age sqlite3; do
  command -v "$tool" >/dev/null || { echo "Install '$tool' first (sudo apt install $tool)" >&2; exit 1; }
done
grep -q '^BACKUP_AGE_RECIPIENT=.\+' "$APP_DIR/.env" || { echo "Set BACKUP_AGE_RECIPIENT in $APP_DIR/.env" >&2; exit 1; }
grep -q '^BACKUP_RCLONE_REMOTE=.\+' "$APP_DIR/.env" || { echo "Set BACKUP_RCLONE_REMOTE in $APP_DIR/.env" >&2; exit 1; }

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
