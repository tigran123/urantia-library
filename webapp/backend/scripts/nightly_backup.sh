#!/bin/bash
# Nightly online-safe backup of /Books/.data/db/lib.db using the SQLite
# .backup command (WAL-safe; works while the backend is running).
#
# Writes to /Books/.data/db/backups/lib-YYYYMMDD.db and prunes the
# directory back to the most recent RETAIN files. Re-running on the same
# UTC day overwrites that day's file (single snapshot per calendar day).
#
# Driven by webapp/urantia-library-backup.timer.

set -euo pipefail

DB="${LIB_DB:-/Books/.data/db/lib.db}"
BACKUP_DIR="${BACKUP_DIR:-/Books/.data/db/backups}"
RETAIN="${RETAIN:-14}"

if [[ ! -f "$DB" ]]; then
    echo "nightly_backup: source DB not found: $DB" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

stamp=$(date -u +%Y%m%d)
dest="$BACKUP_DIR/lib-${stamp}.db"
tmp="${dest}.partial"

# .backup is the online backup API; safe to run against a live, WAL-mode DB.
# Write to a partial path first so a crash mid-copy doesn't leave a
# half-written file that the prune step would think is good.
rm -f "$tmp"
sqlite3 "$DB" ".backup '$tmp'"
mv -f "$tmp" "$dest"

echo "nightly_backup: wrote $dest"

# Prune: keep the RETAIN newest lib-*.db files, delete the rest.
mapfile -t old < <(
    find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lib-*.db' -printf '%T@ %p\n' \
        | sort -nr \
        | awk -v keep="$RETAIN" 'NR > keep { $1=""; sub(/^ /,""); print }'
)
for f in "${old[@]}"; do
    rm -f -- "$f"
    echo "nightly_backup: pruned $f"
done
