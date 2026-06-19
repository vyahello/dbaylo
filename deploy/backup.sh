#!/usr/bin/env bash
# Off-box backup: a consistent SQLite snapshot + the lab-files directory, bundled,
# age-encrypted to YOUR public key (the private key never lives on this box), and
# uploaded via rclone, with retention. Run nightly by dbaylo-backup.timer
# (deploy/setup-backup.sh installs it).
#
# Config comes from the app .env (no secrets in the repo):
#   BACKUP_RCLONE_REMOTE   where the archive goes. A LOCAL dir now (e.g.
#                          "~/.dbaylo/backups"), or a remote later (e.g.
#                          "b2:my-bucket/dbaylo") — rclone handles both, so switching
#                          to off-box is just this one value.
#   BACKUP_AGE_RECIPIENT   OPTIONAL age/SSH *public* key. Set it and the archive is
#                          encrypted (.tar.age); leave it empty for a plain .tar.gz
#                          (fine for a local backup — the live DB is plaintext anyway).
#   BACKUP_RETENTION_DAYS  days to keep (default 14)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$APP_DIR/.env" ]; then set -a; . "$APP_DIR/.env"; set +a; fi

: "${BACKUP_RCLONE_REMOTE:?set BACKUP_RCLONE_REMOTE in .env (a local dir, or b2:bucket/path)}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
DB_PATH="${BACKUP_DB_PATH:-$APP_DIR/dbaylo.db}"
STORAGE_DIR="${STORAGE_DIR:-data/files}"
case "$STORAGE_DIR" in /*) STORAGE_ABS="$STORAGE_DIR" ;; *) STORAGE_ABS="$APP_DIR/$STORAGE_DIR" ;; esac

STAMP="$(date +%Y%m%d-%H%M%S)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1) consistent online snapshot (safe while the bot has the DB open)
sqlite3 "$DB_PATH" ".backup '$TMP/dbaylo.db'"

# 2) bundle DB + lab files; encrypt only if a recipient is configured
tar_args=(-C "$TMP" dbaylo.db)
if [ -d "$STORAGE_ABS" ]; then
  tar_args+=(-C "$(dirname "$STORAGE_ABS")" "$(basename "$STORAGE_ABS")")
fi
if [ -n "$RECIPIENT" ]; then
  ARCHIVE="dbaylo-$STAMP.tar.age"
  printf '%s\n' "$RECIPIENT" > "$TMP/recipients.txt"
  tar -cf - "${tar_args[@]}" | age -R "$TMP/recipients.txt" -o "$TMP/$ARCHIVE"
else
  ARCHIVE="dbaylo-$STAMP.tar.gz"
  tar -czf "$TMP/$ARCHIVE" "${tar_args[@]}"
fi

# 3) store (local dir or remote) + prune old backups (rclone handles local paths too)
rclone copyto "$TMP/$ARCHIVE" "$BACKUP_RCLONE_REMOTE/$ARCHIVE"
rclone delete --min-age "${RETENTION_DAYS}d" "$BACKUP_RCLONE_REMOTE" || true

echo "backup ok: $ARCHIVE -> $BACKUP_RCLONE_REMOTE (retention ${RETENTION_DAYS}d," \
     "encryption: $([ -n "$RECIPIENT" ] && echo age || echo none))"
