#!/usr/bin/env bash
# Off-box backup: a consistent SQLite snapshot + the lab-files directory, bundled,
# age-encrypted to YOUR public key (the private key never lives on this box), and
# uploaded via rclone, with retention. Run nightly by dbaylo-backup.timer
# (deploy/setup-backup.sh installs it).
#
# Config comes from the app .env (no secrets in the repo):
#   BACKUP_AGE_RECIPIENT   age or SSH *public* key to encrypt to (e.g. "ssh-ed25519 AAAA...")
#   BACKUP_RCLONE_REMOTE   rclone remote:path, e.g. "b2:my-bucket/dbaylo"
#   BACKUP_RETENTION_DAYS  off-box days to keep (default 14)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$APP_DIR/.env" ]; then set -a; . "$APP_DIR/.env"; set +a; fi

: "${BACKUP_AGE_RECIPIENT:?set BACKUP_AGE_RECIPIENT in .env (your age/SSH public key)}"
: "${BACKUP_RCLONE_REMOTE:?set BACKUP_RCLONE_REMOTE in .env (e.g. b2:bucket/dbaylo)}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
DB_PATH="${BACKUP_DB_PATH:-$APP_DIR/dbaylo.db}"
STORAGE_DIR="${STORAGE_DIR:-data/files}"
case "$STORAGE_DIR" in /*) STORAGE_ABS="$STORAGE_DIR" ;; *) STORAGE_ABS="$APP_DIR/$STORAGE_DIR" ;; esac

STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="dbaylo-$STAMP.tar.age"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1) consistent online snapshot (safe while the bot has the DB open)
sqlite3 "$DB_PATH" ".backup '$TMP/dbaylo.db'"

# 2) bundle DB + lab files, encrypt to your public key
printf '%s\n' "$BACKUP_AGE_RECIPIENT" > "$TMP/recipients.txt"
tar_args=(-cf - -C "$TMP" dbaylo.db)
if [ -d "$STORAGE_ABS" ]; then
  tar_args+=(-C "$(dirname "$STORAGE_ABS")" "$(basename "$STORAGE_ABS")")
fi
tar "${tar_args[@]}" | age -R "$TMP/recipients.txt" -o "$TMP/$ARCHIVE"

# 3) upload off-box, then prune old backups
rclone copyto "$TMP/$ARCHIVE" "$BACKUP_RCLONE_REMOTE/$ARCHIVE"
rclone delete --min-age "${RETENTION_DAYS}d" "$BACKUP_RCLONE_REMOTE" || true

echo "backup ok: $ARCHIVE -> $BACKUP_RCLONE_REMOTE (retention ${RETENTION_DAYS}d)"
