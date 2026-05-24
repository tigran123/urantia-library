#!/usr/bin/env python3
"""
Backfill the books.size column from the content-addressable vault.

For every row in `books` with size IS NULL, stat /Books/.data/<id> and write
the byte size back. Idempotent: re-run any time to fill in newly-imported
rows. Reports any rows whose vault file is missing.

Usage:
    python3 backfill_sizes.py            # backfill missing rows
    python3 backfill_sizes.py --all      # re-stat every row (force refresh)
    python3 backfill_sizes.py --dry-run  # report what would change, write nothing
"""
import argparse
import os
import sqlite3
import sys

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")
DB_PATH = os.path.join(BOOKS_DIR, ".data", "db", "lib.db")
VAULT_DIR = os.path.join(BOOKS_DIR, ".data")
BATCH = 500  # commit every N rows so a long run shows progress and a Ctrl-C keeps partial work


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="re-stat every book row, not just rows where size IS NULL")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change, write nothing")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"error: database not found at {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    cur = conn.cursor()

    where = "" if args.all else " WHERE size IS NULL"
    cur.execute(f"SELECT id FROM books{where}")
    ids = [row[0] for row in cur.fetchall()]
    total = len(ids)
    if total == 0:
        print("nothing to backfill (all rows already have size).")
        return 0

    print(f"scanning {total} row(s) under {VAULT_DIR}{'  (dry run)' if args.dry_run else ''}")

    missing: list[str] = []
    updated = 0
    unchanged = 0
    for i, hash_id in enumerate(ids, 1):
        vault_path = os.path.join(VAULT_DIR, hash_id)
        try:
            size = os.path.getsize(vault_path)
        except OSError:
            missing.append(hash_id)
            continue

        if args.dry_run:
            updated += 1
        else:
            cur.execute(
                "UPDATE books SET size = ? WHERE id = ? AND (size IS NULL OR size != ?)",
                (size, hash_id, size),
            )
            if cur.rowcount:
                updated += 1
            else:
                unchanged += 1

        if not args.dry_run and i % BATCH == 0:
            conn.commit()
            print(f"  {i}/{total}  updated={updated}  missing={len(missing)}")

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"\ndone: {total} scanned, {updated} {'would update' if args.dry_run else 'updated'}, "
          f"{unchanged} unchanged, {len(missing)} missing vault file")

    if missing:
        print("\nrows whose /Books/.data/<id> file is missing:")
        for h in missing[:50]:
            print(f"  {h}")
        if len(missing) > 50:
            print(f"  ... and {len(missing) - 50} more")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
