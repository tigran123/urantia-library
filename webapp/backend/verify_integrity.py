#!/usr/bin/env python3
"""Standalone full integrity scan of the content-addressable vault.

This is the CLI equivalent of the Admin → Integrity "full scan" (the
`/api/admin/integrity/*` subsystem in main.py). It runs without a browser, a
logged-in admin session, or a running backend — useful over SSH, from cron, or
after restoring a backup.

For every row in `books` it runs the same per-book checks as
`main._verify_book_sync(..., mode="full")`:
  1. db_row            — the books row exists (always true here; we iterate books)
  2. data_file_exists  — /Books/.data/<id> is a regular file
  3. data_file_size    — that file is > 0 bytes
  4. locations_present — at least one book_locations row points at it
  5. symlinks_resolve  — each book_locations.symlink_path is a real symlink that
                         resolves into the vault to the correct hash
  6. hash_match        — recompute BLAKE2b over the whole file and compare to <id>

The hash file *is* its content: BLAKE2b digest (default 64 bytes → 128 hex
chars). A single flipped bit anywhere in a book/audio/video file changes the
digest and is reported as `hash_mismatch`.

It also runs a reverse "orphan" sweep: vault files (/Books/.data/<128-hex>) with
no matching books row (normally reimport_orphans.py's concern), for a complete
two-way picture. Use --skip-orphans to suppress it.

STRICTLY READ-ONLY: the DB is opened mode=ro and no last_verified_* columns are
written, so Admin → Integrity does NOT reflect standalone runs.

Usage:
    python verify_integrity.py                  # full scan + orphan sweep
    python verify_integrity.py --json out.json  # also write a machine-readable report
    python verify_integrity.py --skip-orphans   # per-book checks only
    python verify_integrity.py --progress-every 100   # progress cadence (default 50)

Exit codes:
    0  fully clean (no per-book failures and no orphans)
    1  integrity failures and/or orphan vault files found
    2  database not found
  130  interrupted (Ctrl-C); partial summary printed
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")
DATA_DIR = os.path.join(BOOKS_DIR, ".data")
DB_PATH = os.path.join(DATA_DIR, "db", "lib.db")

_VERIFY_CHUNK = 8 * 1024 * 1024  # match main._VERIFY_CHUNK
HASH_RE = re.compile(r"^[0-9a-f]{128}$")  # match reimport_orphans.HASH_RE


# --- helpers copied verbatim from main.py (source of truth) -----------------

def _blake2b_of_file(path: str) -> str:
    # Verbatim copy of main._blake2b_of_file. Vault files are immutable
    # post-migration, so concurrent reads from downloads + this hash are safe
    # on Linux (POSIX reads don't conflict).
    h = hashlib.blake2b()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_VERIFY_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_vault_hash(symlink_path: str) -> str | None:
    """Verbatim copy of main._resolve_vault_hash. Return the BLAKE2b hash if
    `symlink_path` resolves into the CAS vault (i.e. /Books/.data/<hash>); None
    for unrelated symlinks like /Books/GEMINI.md -> CLAUDE.md."""
    try:
        target = os.path.realpath(symlink_path)
    except OSError:
        return None
    data_root = os.path.realpath(DATA_DIR)
    if not target.startswith(data_root + os.sep):
        return None
    name = os.path.basename(target)
    # Vault entries are flat under .data/ — reject anything nested (e.g. covers/).
    if os.path.dirname(target) != data_root:
        return None
    return name or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- per-book verification (parity with main._verify_book_sync, full mode) ---

def _verify_book(hash_id: str, title: str | None,
                 original_filename: str | None,
                 symlink_paths: list[str]) -> dict:
    """Run the full-mode checks for a single book and return a result dict.

    Mirrors main._verify_book_sync mode="full" exactly (same check names, same
    error precedence), minus the DB write — this tool is read-only.
    `symlink_paths` is the book's pre-fetched book_locations.symlink_path list.
    """
    checks: list[dict] = []
    error: str | None = None

    # 1. db_row — always present here (caller iterates the books table).
    checks.append({"name": "db_row", "ok": True, "detail": None})

    # 2. data_file_exists
    data_path = os.path.join(DATA_DIR, hash_id)
    data_exists = os.path.isfile(data_path)
    checks.append({"name": "data_file_exists", "ok": data_exists,
                   "detail": None if data_exists else data_path})
    if not data_exists:
        error = "data_missing"

    # 3. data_file_size
    size = 0
    if data_exists:
        try:
            size = os.path.getsize(data_path)
        except OSError as e:
            size = 0
            checks.append({"name": "data_file_size", "ok": False, "detail": f"stat failed: {e}"})
            error = error or "data_missing"
        else:
            size_ok = size > 0
            checks.append({"name": "data_file_size", "ok": size_ok,
                           "detail": {"bytes": size}})
            if not size_ok:
                error = error or "empty_file"

    # 4. locations_present
    loc_paths = list(symlink_paths)
    checks.append({"name": "locations_present", "ok": len(loc_paths) > 0,
                   "detail": {"count": len(loc_paths)}})
    if not loc_paths:
        error = error or "locations_missing"

    # 5. symlinks_resolve
    bad_symlinks: list[dict] = []
    for sp in loc_paths:
        full = os.path.join(BOOKS_DIR, sp)
        if not os.path.islink(full):
            bad_symlinks.append({"symlink_path": sp, "reason": "not a symlink or missing"})
            continue
        resolved = _resolve_vault_hash(full)
        if resolved != hash_id:
            bad_symlinks.append({"symlink_path": sp,
                                 "reason": f"resolves to {resolved!r}, expected {hash_id}"})
    checks.append({"name": "symlinks_resolve", "ok": not bad_symlinks,
                   "detail": bad_symlinks if bad_symlinks else None})
    if bad_symlinks:
        error = error or "symlink_broken"

    # 6. hash_match (full mode)
    hashed = False
    if data_exists and size > 0:
        try:
            computed = _blake2b_of_file(data_path)
        except OSError as e:
            checks.append({"name": "hash_match", "ok": False, "detail": f"read failed: {e}"})
            error = error or "data_missing"
        else:
            hashed = True
            match = computed == hash_id
            checks.append({"name": "hash_match", "ok": match,
                           "detail": None if match else f"computed {computed}"})
            if not match:
                error = error or "hash_mismatch"

    return {
        "hash_id": hash_id, "mode": "full", "ok": error is None, "error": error,
        "checks": checks, "verified_at": _now_iso(),
        "title": title, "original_filename": original_filename,
        # extras (not in the API shape): the book's library locations (so a
        # failure report names the affected paths) + progress accounting.
        "locations": loc_paths,
        "size": size, "hashed_bytes": size if hashed else 0,
    }


# --- orphan reverse sweep (parity with reimport_orphans.discover_orphans) ----

def _discover_orphans(book_ids: set[str]) -> list[str]:
    """Vault files /Books/.data/<128-hex> with no matching books row. Mirrors
    reimport_orphans.discover_orphans: non-hash names and subdirectories
    (covers/, db/, avatars/, *.bak …) are skipped by the regex + isfile gate."""
    vault: set[str] = set()
    for name in os.listdir(DATA_DIR):
        if not HASH_RE.match(name):
            continue
        if os.path.isfile(os.path.join(DATA_DIR, name)):
            vault.add(name)
    return sorted(vault - book_ids)


# --- formatting -------------------------------------------------------------

def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f}{unit}" if unit != "B" else f"{int(f)}B"
        f /= 1024
    return f"{f:.1f}TB"


def _human_duration(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _failure_specifics(result: dict) -> str:
    """One-line specifics for a failed book, pulled from the failing check."""
    for chk in result["checks"]:
        if not chk["ok"]:
            detail = chk["detail"]
            return f"{chk['name']}: {detail}"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", metavar="PATH", default=None,
                        help="also write a machine-readable JSON report to PATH")
    parser.add_argument("--skip-orphans", action="store_true",
                        help="skip the reverse sweep for orphan vault files")
    parser.add_argument("--progress-every", type=int, default=50, metavar="N",
                        help="on a TTY the status updates in place (~10x/s); when piped, "
                             "print a fresh line every N books (default 50). 0 disables progress.")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"verify_integrity: database not found at {DB_PATH}", file=sys.stderr)
        return 2

    # Read-only connection: no last_verified_* writes, no risk to the live DB.
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        books = conn.execute(
            "SELECT id, title, original_filename FROM books ORDER BY id"
        ).fetchall()
        locs_by_hash: dict[str, list[str]] = defaultdict(list)
        for hash_id, sp in conn.execute(
            "SELECT hash_id, symlink_path FROM book_locations"
        ):
            locs_by_hash[hash_id].append(sp)
    finally:
        conn.close()

    total = len(books)
    book_ids = {row[0] for row in books}

    # Quick metadata-only pass to learn the total bytes to hash, so the ETA can
    # be projected from throughput (bytes), not book count — books range from
    # tiny texts to multi-GB media, so a count-based ETA would be meaningless.
    total_bytes = 0
    for hash_id, _t, _f in books:
        try:
            total_bytes += os.path.getsize(os.path.join(DATA_DIR, hash_id))
        except OSError:
            pass

    print(f"verify_integrity: full scan of {total} book(s) "
          f"({_human_bytes(total_bytes)}) under {DATA_DIR}\n"
          f"  (re-hashing every vault file with BLAKE2b; read-only)\n")

    results: list[dict] = []
    error_counts: Counter[str] = Counter()
    ok_count = 0
    hashed_bytes = 0
    started = time.monotonic()
    interrupted = False

    # On a TTY, redraw a single status line in place (throttled ~10x/s). When
    # output is piped/redirected (cron, --json > file), \r would be noise, so
    # fall back to a fresh line every --progress-every books. 0 disables both.
    is_tty = sys.stdout.isatty()
    last_draw = 0.0

    def draw_progress(i: int, *, force: bool = False) -> None:
        nonlocal last_draw
        if args.progress_every <= 0:
            return
        now = time.monotonic()
        elapsed = now - started
        remaining = total_bytes - hashed_bytes
        if hashed_bytes > 0 and remaining > 0 and elapsed > 0:
            eta = _human_duration(remaining / (hashed_bytes / elapsed))
        else:
            eta = "0s" if hashed_bytes else "…"
        line = (f"  {i}/{total}  ok={ok_count} fail={i - ok_count}  "
                f"{_human_bytes(hashed_bytes)}/{_human_bytes(total_bytes)}  "
                f"elapsed={_human_duration(elapsed)}  ETA {eta}")
        if is_tty:
            if not force and now - last_draw < 0.1:
                return
            last_draw = now
            sys.stdout.write("\r\033[K" + line)  # \r + clear-to-end-of-line
            sys.stdout.flush()
        elif i % args.progress_every == 0 or i == total:
            print(line)

    try:
        for i, (hash_id, title, original_filename) in enumerate(books, 1):
            r = _verify_book(hash_id, title, original_filename,
                             locs_by_hash.get(hash_id, []))
            results.append(r)
            hashed_bytes += r["hashed_bytes"]
            if r["ok"]:
                ok_count += 1
            else:
                error_counts[r["error"] or "unknown"] += 1
            draw_progress(i)
        if is_tty and total and args.progress_every > 0:
            draw_progress(total, force=True)  # final values before the summary
            sys.stdout.write("\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        interrupted = True
        if is_tty:  # finalize the in-place line so the message starts fresh
            sys.stdout.write("\n")
            sys.stdout.flush()
        print("\nverify_integrity: interrupted — partial results below.\n")

    fail_count = len(results) - ok_count
    elapsed = time.monotonic() - started

    # Reverse sweep: orphan vault files.
    orphans: list[str] = []
    if not args.skip_orphans and not interrupted:
        orphans = _discover_orphans(book_ids)

    # --- summary ---
    print("\n" + "=" * 60)
    print(f"scanned : {len(results)}/{total} book(s)")
    print(f"ok      : {ok_count}")
    print(f"failed  : {fail_count}")
    if error_counts:
        for code, n in sorted(error_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"            {code:<18} {n}")
    print(f"hashed  : {_human_bytes(hashed_bytes)} in {_human_duration(elapsed)}")
    if not args.skip_orphans:
        if interrupted:
            print("orphans : (skipped — scan was interrupted)")
        else:
            print(f"orphans : {len(orphans)} vault file(s) with no books row")

    # --- failure detail ---
    failures = [r for r in results if not r["ok"]]
    if failures:
        print("\nfailures:")
        for r in failures:
            t = r["title"] or r["original_filename"] or "(no title)"
            print(f"  [{r['error']}]  {t!r}")
            print(f"      vault file : {os.path.join(DATA_DIR, r['hash_id'])}")
            spec = _failure_specifics(r)
            if spec:
                print(f"      check      : {spec}")
            locs = r.get("locations") or []
            if locs:
                print(f"      affected paths ({len(locs)}):")
                for sp in locs:
                    print(f"        {os.path.join(BOOKS_DIR, sp)}")
            else:
                print("      affected paths: (none registered in book_locations)")

    if orphans:
        print(f"\norphan vault files (no books row; see reimport_orphans.py):")
        for h in orphans[:50]:
            print(f"  {h}")
        if len(orphans) > 50:
            print(f"  … and {len(orphans) - 50} more")

    # --- optional JSON report ---
    if args.json:
        report = {
            "generated_at": _now_iso(),
            "books_dir": BOOKS_DIR,
            "db_path": DB_PATH,
            "mode": "full",
            "interrupted": interrupted,
            "summary": {
                "total": total,
                "scanned": len(results),
                "ok": ok_count,
                "failed": fail_count,
                "errors": dict(error_counts),
                "hashed_bytes": hashed_bytes,
                "elapsed_seconds": round(elapsed, 1),
                "orphans": len(orphans),
            },
            "results": results,
            "orphans": orphans,
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON report written to {args.json}")

    if interrupted:
        return 130
    return 0 if (fail_count == 0 and not orphans) else 1


if __name__ == "__main__":
    sys.exit(main())
