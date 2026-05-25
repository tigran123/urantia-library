#!/usr/bin/env python3
"""Normalize books.published to year-only (YYYY).

Calibre's ebook-meta emits the publication date as a full ISO
datetime ("2007-05-15T00:00:00+00:00") even when the source only
declared a year. _extract_upload_metadata in main.py stores those
verbatim, which is wrong: books.published is meant to hold a year,
the upload UI's own placeholder says "YYYY or YYYY-MM-DD", and the
Russian label literally translates to "Year of publication".

This script trims any books.published value whose first 4 chars are
a year followed by "-" down to just those 4 chars. Rows already
holding a plain YYYY are untouched. Rows whose first 4 chars don't
parse as a 1000-2099 year are left alone and reported so the admin
can review them by hand.

Usage:
  python normalize_published_years.py                  # default: --scan
  python normalize_published_years.py --scan           # list candidates, no writes
  python normalize_published_years.py --apply          # do it; y/N prompt
  python normalize_published_years.py --apply --yes    # skip prompt
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

DB_PATH = "/Books/.data/db/lib.db"
BACKUP_DIR = "/Books/.data/db/backups"

# Candidate rows: anything shaped "YYYY-…". We further validate the
# year range in Python so we don't trim values like "0000-…" or
# "9999-…" that survived some other extractor bug.
CANDIDATE_WHERE = (
    "published IS NOT NULL "
    "AND length(published) > 4 "
    "AND published GLOB '[0-9][0-9][0-9][0-9]-*'"
)

MIN_YEAR = 1000
MAX_YEAR = 2099


def _open_db(read_only: bool) -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        sys.exit(f"FATAL: {DB_PATH} not found")
    uri = f"file:{DB_PATH}?mode={'ro' if read_only else 'rw'}"
    return sqlite3.connect(uri, uri=True)


def _classify(rows: list[tuple[str, str]]) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Split candidate rows into (eligible, rejected).

    eligible:  [(hash_id, old, new_year)]  — new_year is the trimmed 4-char string
    rejected:  [(hash_id, old)]            — first 4 chars don't parse to 1000..2099
    """
    eligible: list[tuple[str, str, str]] = []
    rejected: list[tuple[str, str]] = []
    for hash_id, old in rows:
        head = old[:4]
        try:
            y = int(head)
        except ValueError:
            rejected.append((hash_id, old))
            continue
        if not (MIN_YEAR <= y <= MAX_YEAR):
            rejected.append((hash_id, old))
            continue
        eligible.append((hash_id, old, head))
    return eligible, rejected


def _scan() -> None:
    with _open_db(read_only=True) as conn:
        rows = conn.execute(
            f"SELECT id, published FROM books WHERE {CANDIDATE_WHERE}"
        ).fetchall()

    eligible, rejected = _classify(rows)
    print(f"Candidates: {len(rows)}")
    print(f"  → eligible (will be trimmed):   {len(eligible)}")
    print(f"  → rejected (year out of range): {len(rejected)}")

    if eligible:
        # Histogram by target year, showing how many rows collapse into each.
        year_counts = Counter(new for (_h, _o, new) in eligible)
        print("\nRows per target year (top 20):")
        for year, n in year_counts.most_common(20):
            print(f"  {year}: {n}")

        # Distinct old → new mappings sorted by frequency, top 10.
        mapping_counts = Counter((old, new) for (_h, old, new) in eligible)
        print("\nMost common (old → new) mappings (top 10):")
        for (old, new), n in mapping_counts.most_common(10):
            print(f"  {old!r:>40} → {new}  ({n} rows)")

    if rejected:
        print("\nRejected rows (left untouched) — first 10:")
        for hash_id, old in rejected[:10]:
            print(f"  {hash_id[:12]}…  published={old!r}")

    print("\nDry run only. Use --apply to write.")


def _backup_db() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = os.path.join(BACKUP_DIR, f"pre-normalize-published-{ts}.bak")
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dst)
    try:
        src.backup(dst_conn)
    finally:
        dst_conn.close()
        src.close()
    return dst


def _apply(skip_prompt: bool) -> None:
    with _open_db(read_only=True) as conn:
        rows = conn.execute(
            f"SELECT id, published FROM books WHERE {CANDIDATE_WHERE}"
        ).fetchall()
    eligible, rejected = _classify(rows)

    if not eligible:
        print("Nothing to do — 0 eligible rows.")
        if rejected:
            print(f"({len(rejected)} rows had unparseable years; left as-is.)")
        return

    print(f"Will update {len(eligible)} rows. {len(rejected)} rejected, left as-is.")
    if not skip_prompt:
        ans = input("Proceed? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Aborted.")
            return

    backup_path = _backup_db()
    print(f"Backup written: {backup_path}")

    # One transaction, single SQL statement — uses substr() server-side
    # so the same WHERE the scan classified as eligible is what's
    # updated, and we don't ship 3k+ individual updates over the wire.
    eligible_where = (
        f"{CANDIDATE_WHERE} "
        f"AND CAST(substr(published, 1, 4) AS INTEGER) BETWEEN {MIN_YEAR} AND {MAX_YEAR}"
    )
    with _open_db(read_only=False) as conn:
        cur = conn.execute(
            f"UPDATE books SET published = substr(published, 1, 4) WHERE {eligible_where}"
        )
        affected = cur.rowcount
        conn.commit()
    print(f"Updated {affected} rows.")
    if affected != len(eligible):
        print(
            f"WARNING: classifier counted {len(eligible)} eligible rows but "
            f"UPDATE affected {affected}. Re-scan to investigate."
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--scan", action="store_true", help="(default) list candidates, no writes")
    g.add_argument("--apply", action="store_true", help="run the UPDATE after a snapshot")
    ap.add_argument("--yes", action="store_true", help="skip the y/N prompt on --apply")
    args = ap.parse_args()

    if args.apply:
        _apply(skip_prompt=args.yes)
    else:
        _scan()


if __name__ == "__main__":
    main()
