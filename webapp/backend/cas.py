"""Foundation module (extracted from main.py): content-addressable-storage
helpers — vault-hash resolution, BLAKE2b hashing, per-book integrity
verification, format detection by bytes, upload metadata/cover extraction, and
text-byte reads. Depends on config (+ models/database). No import cycle: it does
NOT import main."""
import os
import re
import subprocess
import hashlib
import shutil
import uuid
import zipfile
import json
import logging
from typing import Optional
from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session
import models
from config import (
    _AUDIO_EXTS, _VIDEO_EXTS, _IMAGE_EXTS, _now_iso, CODE_EXTENSIONS,
)


def _m():
    """Lazy handle to the fully-imported `main` module. The test suite redirects
    the library filesystem by `monkeypatch.setattr(main, "BOOKS_DIR", ...)`
    (likewise DATA_DIR / STAGING_DIR), so these mutable runtime paths must be read
    through `main` — the patch target — rather than bound at import. main re-exports
    them from config, so production reads the same config values. Call-time import:
    no load-time cas<->main cycle."""
    import main
    return main


def _ensure_writable_dir(path: str) -> None:
    """Lazy mkdir — module-load shouldn't fail on a read-only FS just because
    a dir doesn't exist yet; surface a clean 500 only at first actual use."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot create {path}: {e}. Service needs write access "
                   f"(extend ReadWritePaths= in urantia-library.service).",
        )


def _validate_cover_upload(file: UploadFile) -> None:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Invalid image file")


def _resolve_vault_hash(symlink_path: str) -> str | None:
    """Return the BLAKE2b hash if `symlink_path` resolves into the CAS vault
    (i.e. /Books/.data/<hash>); None for unrelated symlinks like
    /Books/GEMINI.md -> CLAUDE.md."""
    try:
        target = os.path.realpath(symlink_path)
    except OSError:
        return None
    data_root = os.path.realpath(_m().DATA_DIR)
    if not target.startswith(data_root + os.sep):
        return None
    name = os.path.basename(target)
    # Vault entries are flat under .data/ — reject anything nested (e.g. covers/).
    if os.path.dirname(target) != data_root:
        return None
    return name or None

# ---------- Integrity verification ----------

_VERIFY_CHUNK = 8 * 1024 * 1024  # match migrate_library.py

def _blake2b_of_file(path: str) -> str:
    # Vault files are immutable post-migration, so concurrent reads from
    # downloads + this hash are safe on Linux (POSIX reads don't conflict).
    h = hashlib.blake2b()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_VERIFY_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()

def _verify_book_sync(hash_id: str, mode: str, db: Session) -> dict:
    """Run integrity checks for a single book and persist the result.

    Returns a JSON-serialisable dict (see plan §B). Caller is responsible for
    running this inside a worker thread (asyncio.to_thread) because the
    full-mode hash recompute is CPU/IO-bound.
    """
    if mode not in ("quick", "full"):
        mode = "quick"

    checks: list[dict] = []
    error: Optional[str] = None
    db_update_failed = False

    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    checks.append({"name": "db_row", "ok": book is not None,
                   "detail": None if book else "no books row for this hash_id"})
    if not book:
        return {
            "hash_id": hash_id, "mode": mode, "ok": False, "error": "book_missing",
            "checks": checks, "verified_at": _now_iso(),
            "title": None, "original_filename": None, "db_update_failed": False,
        }

    title = book.title
    original_filename = book.original_filename

    data_path = os.path.join(_m().DATA_DIR, hash_id)
    data_exists = os.path.isfile(data_path)
    checks.append({"name": "data_file_exists", "ok": data_exists,
                   "detail": None if data_exists else data_path})
    if not data_exists:
        error = "data_missing"

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

    locs = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == hash_id
    ).all()
    loc_paths = [r[0] for r in locs]
    checks.append({"name": "locations_present", "ok": len(loc_paths) > 0,
                   "detail": {"count": len(loc_paths)}})
    if not loc_paths:
        error = error or "locations_missing"

    bad_symlinks: list[dict] = []
    for sp in loc_paths:
        full = os.path.join(_m().BOOKS_DIR, sp)
        if not os.path.islink(full):
            bad_symlinks.append({"symlink_path": sp, "reason": "not a symlink or missing"})
            continue
        resolved = _resolve_vault_hash(full)
        if resolved != hash_id:
            bad_symlinks.append({"symlink_path": sp,
                                 "reason": f"resolves to {resolved!r}, expected {hash_id}"})
    checks.append({"name": "symlinks_resolve",
                   "ok": not bad_symlinks,
                   "detail": bad_symlinks if bad_symlinks else None})
    if bad_symlinks:
        error = error or "symlink_broken"

    if mode == "full" and data_exists and size > 0:
        try:
            computed = _blake2b_of_file(data_path)
        except OSError as e:
            checks.append({"name": "hash_match", "ok": False, "detail": f"read failed: {e}"})
            error = error or "data_missing"
        else:
            match = computed == hash_id
            checks.append({"name": "hash_match", "ok": match,
                           "detail": None if match else f"computed {computed}"})
            if not match:
                error = error or "hash_mismatch"

    ok = error is None
    verified_at = _now_iso()

    try:
        book.last_verified_at = verified_at
        book.last_verified_ok = ok
        book.last_verified_mode = mode
        book.last_verified_error = None if ok else error
        db.commit()
    except Exception as e:
        logging.warning("failed to persist last_verified_* for %s: %s", hash_id, e)
        db.rollback()
        db_update_failed = True

    return {
        "hash_id": hash_id, "mode": mode, "ok": ok, "error": error,
        "checks": checks, "verified_at": verified_at,
        "title": title, "original_filename": original_filename,
        "db_update_failed": db_update_failed,
    }

def _staged_reads_as(file_path: str, suffix: str) -> bool | None:
    """Does the staged file's *bytes* genuinely parse as `suffix`? Returns
    True/False for formats we can probe, or None when there's no probe (the
    caller then keeps the lexical "must keep extension" lock). Reuses the same
    readers the viewers use, so preview and commit never disagree. The zip
    variants require an actual zip (and plain variants require a non-zip) so the
    committed symlink's extension always matches how the live readers unzip."""
    suffix = suffix.lower()
    try:
        if suffix == ".pdf":
            with open(file_path, "rb") as f:
                return f.read(5) == b"%PDF-"
        if suffix == ".djvu":
            with open(file_path, "rb") as f:
                return f.read(4) == b"AT&T"  # DjVu IFF magic 'AT&TFORM'
        if suffix == ".epub":
            if not zipfile.is_zipfile(file_path):
                return False
            with zipfile.ZipFile(file_path) as zf:
                names = set(zf.namelist())
                if "META-INF/container.xml" in names:
                    return True
                if "mimetype" in names:
                    return zf.read("mimetype").strip() == b"application/epub+zip"
            return False
        if suffix == ".fb2":
            if zipfile.is_zipfile(file_path):
                return False
            with open(file_path, "rb") as f:
                return b"<FictionBook" in f.read(65536)
        if suffix == ".fb2.zip":
            if not zipfile.is_zipfile(file_path):
                return False
            with zipfile.ZipFile(file_path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".fb2"):
                        return b"<FictionBook" in zf.read(name)[:65536]
            return False
        if suffix in (".html", ".htm"):
            if zipfile.is_zipfile(file_path):
                return False
            with open(file_path, "rb") as f:
                low = f.read(65536).lower()
            return b"<html" in low or b"<!doctype html" in low
        if suffix in (".html.zip", ".htm.zip"):
            if not zipfile.is_zipfile(file_path):
                return False
            with zipfile.ZipFile(file_path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith((".html", ".htm")) and not name.endswith("/"):
                        low = zf.read(name)[:65536].lower()
                        return b"<html" in low or b"<!doctype html" in low
            return False
        if suffix == ".svg":
            with open(file_path, "rb") as f:
                return b"<svg" in f.read(65536).lower()
        if suffix in _IMAGE_EXTS:
            with Image.open(file_path) as im:
                im.verify()
            return True
        if suffix in (".txt.zip", ".md.zip", ".markdown.zip"):
            if not zipfile.is_zipfile(file_path):
                return False
            with zipfile.ZipFile(file_path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith((".txt", ".md", ".markdown")) and not name.endswith("/"):
                        return b"\x00" not in zf.read(name)[:8192]
            return False
        if suffix in (".txt", ".md", ".markdown") or suffix in CODE_EXTENSIONS:
            if zipfile.is_zipfile(file_path):
                return False  # a zip can't be served as a plain text file
            with open(file_path, "rb") as f:
                return b"\x00" not in f.read(8192)  # textual = no NUL bytes
    except Exception:
        return False
    return None

def _zip_fb2_inplace(src_path: str) -> str:
    """Compress a freshly-uploaded .fb2 into a deterministic .fb2.zip and
    delete the original. Returns the new (.zip) path. Determinism — fixed
    date_time, fixed external_attr, fixed compression — makes the hash of the
    zip stable so duplicate detection works for repeat uploads."""
    dst = src_path + ".zip"
    inner_name = os.path.basename(src_path)
    info = zipfile.ZipInfo(filename=inner_name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with open(src_path, "rb") as fin, zipfile.ZipFile(dst, "w") as zf:
        with zf.open(info, "w", force_zip64=True) as zout:
            while True:
                chunk = fin.read(1024 * 1024)
                if not chunk:
                    break
                zout.write(chunk)
    os.remove(src_path)
    return dst

def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def _extract_upload_metadata(src_path: str, fmt: str) -> dict:
    """Port of the metadata extraction tree from migrate_library.py. Returns a
    dict with keys matching the Book columns (description maps from annotation
    so callers can drop it straight into BookUpdate). Named '_upload_' to avoid
    collision with the FB2-specific _extract_metadata defined later in main.py."""
    out = {
        "title": None, "author": None, "publisher": None, "published": None,
        "description": None, "tags": None, "series": None, "languages": None,
        "identifiers": None,
    }
    try:
        if fmt == "pdf":
            r = _run(["pdfinfo", src_path], timeout=10)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if line.startswith("Title:"):
                        v = line.split(":", 1)[1].strip()
                        if v:
                            out["title"] = v
                    elif line.startswith("Author:"):
                        v = line.split(":", 1)[1].strip()
                        if v:
                            out["author"] = v
        elif fmt == "djvu":
            r = _run(["djvused", "-e", "print-meta", src_path], timeout=10)
            if r.returncode == 0:
                import ast as _ast
                for line in r.stdout.splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) != 2:
                        continue
                    key = parts[0].strip().lower()
                    raw_val = parts[1].strip()
                    try:
                        val = _ast.literal_eval("b" + raw_val).decode("utf-8").strip()
                    except Exception:
                        val = raw_val.strip('" ')
                    if not val:
                        continue
                    if key == "title": out["title"] = val
                    elif key == "author": out["author"] = val
                    elif key == "publisher": out["publisher"] = val
                    elif key == "year": out["published"] = val
                    elif key == "keywords": out["tags"] = val
                    elif key == "descr": out["description"] = val
                    elif key == "isbn":
                        out["identifiers"] = val if val.startswith("isbn:") else f"isbn:{val}"
                    elif key == "lang": out["languages"] = val.lower()
        elif fmt in _AUDIO_EXTS or fmt in _VIDEO_EXTS:
            r = _run(["ffprobe", "-v", "error", "-print_format", "json",
                     "-show_format", src_path], timeout=10)
            if r.returncode == 0:
                data = json.loads(r.stdout or "{}")
                tags = {k.lower(): v for k, v in (data.get("format", {}).get("tags") or {}).items()}
                if tags.get("title"): out["title"] = tags["title"]
                if tags.get("artist"): out["author"] = tags["artist"]
                elif tags.get("album_artist"): out["author"] = tags["album_artist"]
                if tags.get("album"): out["series"] = tags["album"]
                if tags.get("date"):
                    d = tags["date"]
                    out["published"] = d[:4] if re.match(r"^\d{4}", d) else d
                if tags.get("genre"): out["tags"] = tags["genre"]
                if tags.get("comment"): out["description"] = tags["comment"]
        else:
            # ebook-meta dispatches on extension; ensure src_path keeps its name.
            r = _run(["ebook-meta", src_path], timeout=15)
            if r.returncode == 0:
                current_key = None
                comments_buffer: list[str] = []
                for line in r.stdout.splitlines():
                    m = re.match(r"^([A-Za-z\(\)]+)\s*:\s*(.*)", line)
                    if m:
                        raw_key, val = m.groups()
                        key = raw_key.strip().lower()
                        val = val.strip()
                        if val == "Unknown":
                            val = ""
                        if key == "title" and val: out["title"] = val
                        elif key == "author(s)" and val: out["author"] = re.sub(r"\[.*?\]", "", val).strip()
                        elif key == "publisher" and val: out["publisher"] = val
                        elif key == "tags" and val: out["tags"] = val
                        elif key == "series" and val: out["series"] = val
                        elif key == "languages" and val: out["languages"] = val.lower()
                        elif key == "published" and val:
                            # ebook-meta emits a full ISO datetime
                            # ("2007-05-15T00:00:00+00:00") even when the
                            # source only declared a year. The column is
                            # year-shaped — trim to the leading 4 digits.
                            out["published"] = val[:4] if re.match(r"^\d{4}-", val) else val
                        elif key == "identifiers" and val: out["identifiers"] = val
                        elif key == "comments":
                            current_key = "comments"
                            if val:
                                comments_buffer.append(val)
                        else:
                            current_key = None
                    elif current_key == "comments":
                        comments_buffer.append(line.strip())
                if comments_buffer:
                    out["description"] = "\n".join(comments_buffer).strip()
    except Exception as e:
        logging.warning("metadata extraction failed for %s: %s", src_path, e)
    return out

def _extract_media_meta(path: str) -> tuple[Optional[float], Optional[int]]:
    """Audio/video length (seconds) and container bitrate (bits/sec) via ffprobe.
    Reads the extension-less vault file by content, so it works on /Books/.data/<id>
    directly. Returns (None, None) when ffprobe is unavailable or the values are
    missing/unparseable. These are intrinsic file facts — derive them server-side,
    never trust a client-supplied value."""
    try:
        r = _run(["ffprobe", "-v", "error", "-print_format", "json",
                  "-show_format", path], timeout=10)
        if r.returncode != 0:
            return (None, None)
        fmt = json.loads(r.stdout or "{}").get("format") or {}
        raw_dur, raw_br = fmt.get("duration"), fmt.get("bit_rate")
        duration = float(raw_dur) if raw_dur not in (None, "", "N/A") else None
        bitrate = int(raw_br) if raw_br not in (None, "", "N/A") else None
        # Guard against ffprobe's occasional 0/garbage on streams it can't size.
        if duration is not None and duration <= 0:
            duration = None
        if bitrate is not None and bitrate <= 0:
            bitrate = None
        return (duration, bitrate)
    except Exception as e:
        logging.warning("media meta extraction failed for %s: %s", path, e)
        return (None, None)

def _extract_cover_to(src_path: str, fmt: str, dest_jpg: str) -> Optional[tuple[int, int]]:
    """Run the format-appropriate cover extraction, resize to 300px-wide JPEG
    written at dest_jpg. Returns (width, height) of the saved cover, or None on
    failure."""
    tmp_dir = os.path.join(_m().STAGING_DIR, f".cover-{uuid.uuid4().hex}")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        raw = None
        if fmt == "djvu":
            raw = os.path.join(tmp_dir, "cover.ppm")
            r = _run(["ddjvu", "-format=ppm", "-pages=1", src_path, raw], timeout=30)
            if r.returncode != 0 or not os.path.exists(raw):
                return None
        elif fmt == "pdf":
            r = _run(["pdftoppm", "-singlefile", src_path, os.path.join(tmp_dir, "cover")], timeout=30)
            if r.returncode != 0:
                return None
            for cand in ("cover.ppm", "cover.jpg", "cover.png"):
                p = os.path.join(tmp_dir, cand)
                if os.path.exists(p):
                    raw = p
                    break
            if raw is None:
                return None
        elif fmt in ("jpg", "jpeg", "png", "webp"):
            # The "book" file IS the image — use it directly as the cover source.
            raw = src_path
        elif fmt in ("txt", "txt.zip", "rtf", "html"):
            # No reliable cover for these — ebook-meta would either fail or
            # produce nothing useful. Skip cleanly so the upload continues
            # without a cover (the UI shows the striped placeholder).
            return None
        elif fmt in _AUDIO_EXTS:
            raw = os.path.join(tmp_dir, "cover.jpg")
            r = _run(["ffmpeg", "-y", "-i", src_path, "-map", "0:v:0",
                     "-frames:v", "1", raw], timeout=30)
            if r.returncode != 0 or not os.path.exists(raw):
                return None
        elif fmt in _VIDEO_EXTS:
            raw = os.path.join(tmp_dir, "cover.jpg")
            # Grab a frame ~3s in; ffmpeg falls back to the first frame for
            # very short clips.
            r = _run(["ffmpeg", "-y", "-ss", "00:00:03", "-i", src_path,
                     "-frames:v", "1", raw], timeout=30)
            if r.returncode != 0 or not os.path.exists(raw):
                return None
        else:
            raw = os.path.join(tmp_dir, "cover.jpg")
            r = _run(["ebook-meta", src_path, f"--get-cover={raw}"], timeout=30)
            if r.returncode != 0 or not os.path.exists(raw):
                return None
        with Image.open(raw) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w > 300:
                new_h = max(1, int(h * 300 / w))
                im = im.resize((300, new_h), Image.LANCZOS)
                w, h = im.size
            os.makedirs(os.path.dirname(dest_jpg), exist_ok=True)
            im.save(dest_jpg, format="JPEG", quality=85)
            return (w, h)
    except Exception as e:
        logging.warning("cover extraction failed for %s: %s", src_path, e)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def _read_text_bytes(file_path: str) -> bytes:
    """Raw bytes of a text/markdown file, transparently unzipping a
    .txt.zip/.md.zip/.markdown.zip wrapper (mirrors _read_fb2_bytes/_read_html_bytes)."""
    if file_path.lower().endswith(".zip"):
        with zipfile.ZipFile(file_path) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".txt", ".md", ".markdown")) and not name.endswith("/"):
                    return zf.read(name)
        raise HTTPException(status_code=422, detail="No text entry inside zip")
    with open(file_path, "rb") as f:
        return f.read()
