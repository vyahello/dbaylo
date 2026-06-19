#!/usr/bin/env bash
# Restore + verify a dbaylo backup. Decrypts with your age/SSH PRIVATE key, extracts
# to a scratch dir, and runs an integrity check — it does NOT overwrite live data.
#
#   # fetch the archive off-box first, e.g.:
#   rclone copy b2:my-bucket/dbaylo/dbaylo-20260619-033000.tar.age .
#   # then verify-restore it (uses ~/.ssh/id_ed25519 by default):
#   bash deploy/restore.sh dbaylo-20260619-033000.tar.age [target_dir]
set -euo pipefail

ARCHIVE="${1:?usage: restore.sh <archive.tar.age|.tar.gz> [target_dir]}"
TARGET="${2:-$HOME/dbaylo-restore}"
IDENTITY="${BACKUP_AGE_IDENTITY:-$HOME/.ssh/id_ed25519}"

mkdir -p "$TARGET"
case "$ARCHIVE" in
  *.tar.age) age -d -i "$IDENTITY" "$ARCHIVE" | tar -C "$TARGET" -xf - ;;  # encrypted
  *.tar.gz)  tar -C "$TARGET" -xzf "$ARCHIVE" ;;                            # plain
  *) echo "unknown archive type: $ARCHIVE (expected .tar.age or .tar.gz)" >&2; exit 1 ;;
esac

echo "== verify =="
sqlite3 "$TARGET/dbaylo.db" 'PRAGMA integrity_check;'
sqlite3 "$TARGET/dbaylo.db" \
  'SELECT "users="||(SELECT count(*) FROM users)||"  lab_reports="||(SELECT count(*) FROM lab_reports);'
ls -1 "$TARGET" | sed 's/^/restored: /'

cat <<NOTE

Verified (read-only) in $TARGET. To make it live:
  sudo systemctl stop dbaylo-bot
  cp "$TARGET/dbaylo.db" "$HOME/dbaylo/dbaylo.db"
  [ -d "$TARGET/files" ] && rsync -a "$TARGET/files/" "$HOME/dbaylo/data/files/"
  sudo systemctl start dbaylo-bot
NOTE
