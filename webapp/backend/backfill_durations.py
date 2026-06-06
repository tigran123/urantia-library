#!/usr/bin/env python3
"""
Backfill the books.duration and books.bitrate columns from the vault.

For every audio/video row in `books` with duration IS NULL, run ffprobe on
/Books/.data/<id> and write back the length (seconds) and container bitrate
(bits/sec). Idempotent: re-run any time to fill in newly-imported rows. Reports
rows whose vault file is missing or that ffprobe can't read.

Audio/video rows are identified by the original_filename extension (the vault
file itself is extension-less; ffprobe sniffs by content). Counterpart to
backfill_sizes.py; the webapp also sets these at import time, so this is the
catch-all for pre-existing rows and anything an import path missed.

Usage:
    python3 backfill_durations.py            # backfill rows where duration IS NULL
    python3 backfill_durations.py --all      # re-probe every audio/video row
    python3 backfill_durations.py --dry-run  # report what would change, write nothing
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")
DB_PATH = os.path.join(BOOKS_DIR, ".data", "db", "lib.db")
VAULT_DIR = os.path.join(BOOKS_DIR, ".data")
BATCH = 200  # commit every N rows so a long run shows progress and a Ctrl-C keeps partial work

# Mirror of main._AUDIO_EXTS / _VIDEO_EXTS (kept inline so this stays stdlib-only).
MEDIA_EXTS = {"mp3", "wav", "ogg", "flac", "m4a", "aac", "mp4", "webm", "mkv", "avi", "mov"}


def probe(path: str) -> tuple[float | None, int | None]:
    """(duration_seconds, bitrate_bps) via ffprobe, or (None, None)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return (None, None)
        fmt = json.loads(r.stdout or "{}").get("format") or {}
        raw_dur, raw_br = fmt.get("duration"), fmt.get("bit_rate")
        dur = float(raw_dur) if raw_dur not in (None, "", "N/A") else None
        br = int(raw_br) if raw_br not in (None, "", "N/A") else None
        if dur is not None and dur <= 0:
            dur = None
        if br is not None and br <= 0:
            br = None
        return (dur, br)
    except Exception:
        return (None, None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="re-probe every audio/video row, not just rows where duration IS NULL")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change, write nothing")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"error: database not found at {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    cur = conn.cursor()

    where = "" if args.all else " WHERE duration IS NULL"
    cur.execute(f"SELECT id, original_filename FROM books{where}")
    rows = [
        (hid, fn) for (hid, fn) in cur.fetchall()
        if "." in (fn or "") and fn.rsplit(".", 1)[1].lower() in MEDIA_EXTS
    ]
    total = len(rows)
    if total == 0:
        print("nothing to backfill (no audio/video rows need duration).")
        return 0

    print(f"probing {total} audio/video row(s) under {VAULT_DIR}{'  (dry run)' if args.dry_run else ''}")

    missing: list[str] = []
    unreadable: list[str] = []
    updated = 0
    for i, (hash_id, _fn) in enumerate(rows, 1):
        vault_path = os.path.join(VAULT_DIR, hash_id)
        if not os.path.isfile(vault_path):
            missing.append(hash_id)
            continue
        dur, br = probe(vault_path)
        if dur is None and br is None:
            unreadable.append(hash_id)
            continue

        updated += 1
        if not args.dry_run:
            cur.execute(
                "UPDATE books SET duration = ?, bitrate = ? WHERE id = ?",
                (dur, br, hash_id),
            )

        if not args.dry_run and i % BATCH == 0:
            conn.commit()
            print(f"  {i}/{total}  updated={updated}  missing={len(missing)}  unreadable={len(unreadable)}")

    if not args.dry_run:
        conn.commit()
    conn.close()

    print(f"\ndone: {total} scanned, {updated} {'would update' if args.dry_run else 'updated'}, "
          f"{len(missing)} missing vault file, {len(unreadable)} unreadable by ffprobe")

    if missing:
        print("\nrows whose /Books/.data/<id> file is missing:")
        for h in missing[:50]:
            print(f"  {h}")
        if len(missing) > 50:
            print(f"  ... and {len(missing) - 50} more")
    return 1 if (missing or unreadable) else 0


if __name__ == "__main__":
    sys.exit(main())
