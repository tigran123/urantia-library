#!/usr/bin/env python3
"""Re-import orphan books from the CAS vault.

After restoring an older lib.db backup, vault files at
/Books/.data/<hash> for books added post-backup have no books or
book_locations rows. This script finds those orphans and registers
them under /Books/Unsorted/ at clearance=100 + needs_review=TRUE so
the admin can move each to its proper topic via the existing admin
"move" UI.

Recovery order after restoring a backup:
  1. cp /Books/.data/db/backups/lib-YYYYMMDD.db /Books/.data/db/lib.db
  2. python migrate.py            # bring DB to current schema_version
  3. python reimport_orphans.py --apply  # register vault orphans

What this script CANNOT recover:
  - The original book_locations.symlink_path (no source-tree mirror,
    no .htaccess). Orphans land in /Books/Unsorted/ for manual filing.
  - Per-user state (favorites, annotations, ratings, reading_progress,
    comments) for orphan books — gone with the restored DB.
  - Hand-edited title/author/clearance overrides — only metadata
    extractable from the file bytes themselves is restored.

Usage:
  python reimport_orphans.py                            # default: --scan
  python reimport_orphans.py --scan                     # list orphans, no writes
  python reimport_orphans.py --apply                    # re-import all; y/N prompt
  python reimport_orphans.py --apply --yes              # skip prompt
  python reimport_orphans.py --apply --hash X [--hash Y …]   # specific hashes only
"""
import argparse
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone

# Importing main runs its module-level code, including
# verify_schema_version — so the DB must already be at the current
# EXPECTED_SCHEMA_VERSION (run migrate.py first if you just restored).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (  # noqa: E402
    _extract_upload_metadata,
    _extract_cover_to,
    DATA_DIR,
    COVERS_DIR,
    BOOKS_DIR,
)

DB_PATH = os.path.join(DATA_DIR, "db", "lib.db")
UNSORTED_REL = "Unsorted"
UNSORTED_ABS = os.path.join(BOOKS_DIR, UNSORTED_REL)
HASH_RE = re.compile(r"^[0-9a-f]{128}$")

# MIME → (canonical fmt, extension). Extension is used both for the
# parking filename under /Books/Unsorted/ and for the synthetic
# symlink name we feed to ebook-meta (which dispatches on extension).
MIME_TO_FMT: dict[str, tuple[str, str]] = {
    "application/pdf":                ("pdf", "pdf"),
    "application/epub+zip":           ("epub", "epub"),
    "image/vnd.djvu":                 ("djvu", "djvu"),
    "image/x.djvu":                   ("djvu", "djvu"),
    "image/x-djvu":                   ("djvu", "djvu"),
    "application/x-mobipocket-ebook": ("mobi", "mobi"),
    "application/vnd.amazon.ebook":   ("azw3", "azw3"),
    "image/jpeg":                     ("jpg", "jpg"),
    "image/png":                      ("png", "png"),
    "image/webp":                     ("webp", "webp"),
    "text/plain":                     ("txt", "txt"),
    "text/html":                      ("html", "html"),
    "application/rtf":                ("rtf", "rtf"),
    "text/rtf":                       ("rtf", "rtf"),
    "text/xml":                       ("fb2", "fb2"),
    "application/xml":                ("fb2", "fb2"),
}

_FS_FORBIDDEN_RE = re.compile(r"[/\x00-\x1f\x7f]")


def _file_mime(path: str) -> str:
    r = subprocess.run(
        ["file", "--mime-type", "-b", path],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return ""
    return r.stdout.strip().lower()


def _detect_zip_inner(vault_path: str) -> tuple[str, str] | None:
    """application/zip with one entry → fb2.zip or txt.zip; else ambiguous."""
    try:
        with zipfile.ZipFile(vault_path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
    except (zipfile.BadZipFile, OSError):
        return None
    if len(names) == 1:
        lower = names[0].lower()
        if lower.endswith(".fb2"):
            return ("fb2.zip", "fb2.zip")
        if lower.endswith(".txt"):
            return ("txt.zip", "txt.zip")
    return None


def detect_format(vault_path: str) -> tuple[str, str] | None:
    mime = _file_mime(vault_path)
    if mime == "application/zip":
        return _detect_zip_inner(vault_path)
    return MIME_TO_FMT.get(mime)


def sanitize_filename(name: str, fallback: str) -> str:
    """Strip filesystem-hostile chars; keep Unicode. Empty → fallback."""
    if not name:
        return fallback
    n = unicodedata.normalize("NFC", name)
    n = _FS_FORBIDDEN_RE.sub("", n)
    n = n.strip().strip(".").strip()
    return n or fallback


def unique_in_unsorted(filename: str) -> str:
    if not os.path.lexists(os.path.join(UNSORTED_ABS, filename)):
        return filename
    if filename.lower().endswith(".fb2.zip"):
        base, ext = filename[:-8], ".fb2.zip"
    elif filename.lower().endswith(".txt.zip"):
        base, ext = filename[:-8], ".txt.zip"
    else:
        base, ext_part = os.path.splitext(filename)
        ext = ext_part
    i = 2
    while True:
        candidate = f"{base}-{i}{ext}"
        if not os.path.lexists(os.path.join(UNSORTED_ABS, candidate)):
            return candidate
        i += 1


def discover_orphans(conn: sqlite3.Connection) -> list[str]:
    in_db = {row[0] for row in conn.execute("SELECT id FROM books")}
    vault: set[str] = set()
    for name in os.listdir(DATA_DIR):
        if not HASH_RE.match(name):
            continue
        if os.path.isfile(os.path.join(DATA_DIR, name)):
            vault.add(name)
    return sorted(vault - in_db)


def scan_orphan(hash_id: str) -> dict:
    vault_path = os.path.join(DATA_DIR, hash_id)
    mime = _file_mime(vault_path)
    fmt_info = detect_format(vault_path)
    if fmt_info is None:
        return {
            "hash": hash_id, "mime": mime, "fmt": None,
            "skip_reason": f"unknown/ambiguous MIME ({mime or 'empty'})",
        }
    fmt, ext = fmt_info
    tmp = tempfile.mkdtemp(prefix="reimport-scan-")
    try:
        staged = os.path.join(tmp, f"book.{ext}")
        os.symlink(vault_path, staged)
        meta = _extract_upload_metadata(staged, fmt)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {
        "hash": hash_id, "mime": mime, "fmt": fmt, "ext": ext,
        "title": meta.get("title"),
        "size": os.path.getsize(vault_path),
        "cover_exists": os.path.exists(os.path.join(COVERS_DIR, f"{hash_id}.jpg")),
    }


def apply_orphan(conn: sqlite3.Connection, hash_id: str) -> dict:
    vault_path = os.path.join(DATA_DIR, hash_id)
    if not os.path.isfile(vault_path):
        return {"hash": hash_id, "ok": False, "reason": "vault file missing"}
    fmt_info = detect_format(vault_path)
    if fmt_info is None:
        return {"hash": hash_id, "ok": False, "reason": "unknown/ambiguous MIME"}
    fmt, ext = fmt_info

    tmp = tempfile.mkdtemp(prefix="reimport-")
    symlink_created: str | None = None
    try:
        staged = os.path.join(tmp, f"book.{ext}")
        os.symlink(vault_path, staged)

        meta = _extract_upload_metadata(staged, fmt)

        cover_path = os.path.join(COVERS_DIR, f"{hash_id}.jpg")
        if not os.path.exists(cover_path):
            _extract_cover_to(staged, fmt, cover_path)

        base = sanitize_filename(meta.get("title") or "", f"unsorted-{hash_id[:8]}")
        filename = unique_in_unsorted(f"{base}.{ext}")
        symlink_rel_path = f"{UNSORTED_REL}/{filename}"
        symlink_abs_path = os.path.join(UNSORTED_ABS, filename)

        os.makedirs(UNSORTED_ABS, exist_ok=True)
        os.symlink(os.path.relpath(vault_path, UNSORTED_ABS), symlink_abs_path)
        symlink_created = symlink_abs_path

        import_date = datetime.fromtimestamp(
            os.stat(vault_path).st_mtime, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        size = os.path.getsize(vault_path)

        conn.execute(
            "INSERT INTO books "
            "  (id, original_filename, title, author, publisher, published, "
            "   description, tags, series, languages, identifiers, "
            "   needs_review, clearance, import_date, size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 100, ?, ?)",
            (
                hash_id, filename,
                meta.get("title") or os.path.splitext(filename)[0],
                meta.get("author"), meta.get("publisher"), meta.get("published"),
                meta.get("description"), meta.get("tags"), meta.get("series"),
                meta.get("languages"), meta.get("identifiers"),
                import_date, size,
            ),
        )
        conn.execute(
            "INSERT INTO book_locations (symlink_path, hash_id) VALUES (?, ?)",
            (symlink_rel_path, hash_id),
        )
        conn.commit()
        return {
            "hash": hash_id, "ok": True, "fmt": fmt,
            "path": symlink_rel_path, "title": meta.get("title"),
        }
    except Exception as e:
        conn.rollback()
        if symlink_created and os.path.islink(symlink_created):
            try:
                os.remove(symlink_created)
            except OSError:
                pass
        return {"hash": hash_id, "ok": False, "reason": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fmt_scan_line(info: dict) -> str:
    h = info["hash"][:16]
    if info.get("fmt") is None:
        return f"  {h}...  SKIP  {info.get('skip_reason', 'unknown')}"
    size_kb = info.get("size", 0) // 1024
    title = info.get("title") or "(no title)"
    cover = "yes" if info.get("cover_exists") else "no "
    return (f"  {h}...  fmt={info['fmt']:<8} size={size_kb:>7}KB  "
            f"cover={cover}  title={title!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--scan", action="store_true",
                   help="(default) List orphans; no writes.")
    g.add_argument("--apply", action="store_true",
                   help="Re-import orphans into /Books/Unsorted/.")
    parser.add_argument("--hash", action="append", default=[],
                        help="Restrict to specific hashes. May repeat.")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the interactive y/N prompt on --apply.")
    args = parser.parse_args()
    if not args.apply:
        args.scan = True

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if not os.path.exists(DB_PATH):
        print(f"reimport: DB not found at {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        all_orphans = discover_orphans(conn)

        if args.hash:
            wanted = set(args.hash)
            unknown = wanted - set(all_orphans)
            if unknown:
                print("reimport: not orphans (or not in vault): "
                      + ", ".join(sorted(unknown)), file=sys.stderr)
            orphans = [h for h in all_orphans if h in wanted]
        else:
            orphans = all_orphans

        print(f"reimport: {len(all_orphans)} orphan(s) in vault; "
              f"{len(orphans)} selected.")
        if not orphans:
            return 0

        infos = [scan_orphan(h) for h in orphans]
        for info in infos:
            print(fmt_scan_line(info))

        if args.scan:
            return 0

        importable = [i for i in infos if i.get("fmt") is not None]
        skipped = len(infos) - len(importable)
        print(f"\nreimport: would import {len(importable)} orphan(s); "
              f"{skipped} skipped (unknown format).")
        if not importable:
            return 0

        if not args.yes:
            try:
                resp = input("Apply? [y/N] ").strip().lower()
            except EOFError:
                resp = ""
            if resp != "y":
                print("reimport: aborted.")
                return 0

        os.makedirs(UNSORTED_ABS, exist_ok=True)
        ok = fail = 0
        for info in importable:
            result = apply_orphan(conn, info["hash"])
            if result["ok"]:
                ok += 1
                print(f"  [OK]   {info['hash'][:16]}...  -> {result['path']}")
            else:
                fail += 1
                print(f"  [FAIL] {info['hash'][:16]}...  {result['reason']}",
                      file=sys.stderr)
        print(f"\nreimport: applied={ok}, failed={fail}, skipped={skipped}.")
        return 0 if fail == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
