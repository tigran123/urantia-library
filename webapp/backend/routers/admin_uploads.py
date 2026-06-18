"""Router module (extracted from main.py): the admin Add-Book / import wizard —
the multipart upload SSE stream, staging cover/file/format-preview endpoints,
the importable-file expansion, stage-from-server-path, and the final commit that
moves a staged file into the CAS vault. Includes the batch-upload audit folding
helper and the staging-file resolver. Moved verbatim from main.py (no logic
change).

The staging content-preview endpoints reuse CONTENT helpers that still live in
main.py until Wave 3 (`_read_fb2_bytes`, `_convert_fb2`, `_convert_txt`,
`_convert_code`, `_convert_md`, `_read_html_bytes`, `_convert_html`,
`extract_djvu_outline`, `_read_text_file`) — reached via a call-time `import
main` shim."""
import os
import io
import json
import uuid
import shutil
import logging
import asyncio
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, Response
from sqlalchemy.orm import Session
from PIL import Image
import djvu.decode

import models
import schemas
import state
from database import get_db
from config import (
    CODE_EXTENSIONS, _ACCEPTED_BOOK_EXTS, _AUDIO_EXTS, _VIDEO_EXTS,
    _MAX_UPLOAD_BYTES, _MAX_COVER_BYTES, _STAGING_TTL_S, _TOPDIR_SKIPLIST,
    _detect_format, _effective_suffix, _text_inner_ext, _is_recommended_path,
    _now_iso,
)
from deps import require_admin
from paths import _safe_under_books, _safe_path_segment, _safe_subpath
from cas import (
    _extract_upload_metadata, _extract_media_meta, _extract_cover_to,
    _staged_reads_as, _zip_fb2_inplace, _blake2b_of_file,
    _validate_cover_upload, _ensure_writable_dir,
)
from background import _audit, _purge_expired_staging
from serialize import _book_to_admin_detail

router = APIRouter()


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect the
    library root via `monkeypatch.setattr(main, "BOOKS_DIR", ...)` (likewise
    DATA_DIR / STAGING_DIR / COVERS_DIR), so these mutable runtime paths must be
    read through `main` (the patch target). main also still holds the CONTENT
    helpers (Wave 3); reach them via this shim too. Call-time import: no
    load-time cycle."""
    import main
    return main


def _audit_batch_upload(db: Session, actor: models.User, batch_id: str, book: dict, base_dir: str) -> None:
    """Fold one committed book into a single 'batch upload' audit row (one row per
    multi-book Commit-all run), creating it on the first commit and appending on
    the rest. Keeps the audit log compact for multi-volume sets while each book
    still rides its own commit transaction — so the row reflects exactly what was
    committed, even when a batch is only partially completed."""
    now = datetime.now(timezone.utc).timestamp()
    with state._AUDIT_BATCHES_LOCK:
        for b in [b for b, (_i, ts) in state._AUDIT_BATCHES.items() if now - ts > state._AUDIT_BATCH_TTL_S]:
            state._AUDIT_BATCHES.pop(b, None)
        entry = state._AUDIT_BATCHES.get(batch_id)
    row = (db.query(models.AdminAuditLog)
             .filter(models.AdminAuditLog.id == entry[0]).first()) if entry else None
    if row is None:
        details = {"count": 1, "dir": base_dir, "books": [book]}
        row = models.AdminAuditLog(
            created_at=_now_iso(),
            actor_user_id=actor.id,
            action="book.upload",
            target_kind="book_batch",
            target_id=batch_id,
            summary=f"Uploaded 1 book to /{base_dir}" if base_dir else "Uploaded 1 book",
            details_json=json.dumps(details, separators=(",", ":")),
        )
        db.add(row)
        db.flush()  # assign row.id within this transaction
    else:
        try:
            details = json.loads(row.details_json or "{}")
        except json.JSONDecodeError:
            details = {}
        books = (details.get("books") or []) + [book]
        d = details.get("dir") or base_dir
        details = {"count": len(books), "dir": d, "books": books}
        row.details_json = json.dumps(details, separators=(",", ":"))
        row.summary = f"Uploaded {len(books)} books to /{d}" if d else f"Uploaded {len(books)} books"
    with state._AUDIT_BATCHES_LOCK:
        state._AUDIT_BATCHES[batch_id] = (row.id, now)


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _log_entry(level: str, msg: str) -> dict:
    return {
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}",
        "level": level,
        "msg": msg,
    }


def _format_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    i = 0
    f = float(n)
    while f >= 1024.0 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return f"{f:.1f} {units[i]}" if i else f"{int(f)} {units[i]}"


@router.post("/api/admin/books/upload")
async def admin_upload_book(
    file: UploadFile = File(...),
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Multipart upload of a single book file. Returns an SSE stream of `log`
    events ending with a `done` event whose payload either contains
    `extracted_metadata` + `staging_id` (success) or `existing` (duplicate)."""
    _purge_expired_staging()
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="No filename")
    fmt = _detect_format(filename)
    primary_ext = fmt.split(".")[-1]
    if primary_ext not in _ACCEPTED_BOOK_EXTS:
        raise HTTPException(status_code=415, detail=f"Unsupported format: {fmt}")
    owner_id = _admin.id

    # Pre-create the staging dir + buffer the upload to disk *before* we open
    # the generator, so that fatal write errors surface as a clean HTTP error
    # (not a half-streamed SSE response).
    _ensure_writable_dir(_m().STAGING_DIR)
    staging_id = uuid.uuid4().hex
    sdir = os.path.join(_m().STAGING_DIR, staging_id)
    os.makedirs(sdir, exist_ok=True)
    safe_name = os.path.basename(filename)
    dest_path = os.path.join(sdir, safe_name)
    bytes_written = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _MAX_UPLOAD_BYTES:
                    out.close()
                    shutil.rmtree(sdir, ignore_errors=True)
                    raise HTTPException(status_code=413, detail="File too large")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(sdir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    async def stream():
        # Mutable locals so the FB2→FB2.ZIP step can rewrite them mid-stream.
        nonlocal_fmt = fmt
        nonlocal_safe_name = safe_name
        nonlocal_dest_path = dest_path
        nonlocal_bytes = bytes_written
        try:
            size_str = _format_size(nonlocal_bytes)
            yield _sse_event("log", _log_entry("info", "POST /api/admin/books/upload  multipart/form-data"))
            yield _sse_event("log", _log_entry("info", f'Receiving "{nonlocal_safe_name}" ({size_str})'))
            yield _sse_event("log", _log_entry("info", f"Upload complete — {nonlocal_bytes} bytes"))

            if nonlocal_fmt == "fb2":
                yield _sse_event("log", _log_entry("info", "Compressing FB2 → FB2.ZIP for storage…"))
                nonlocal_dest_path = await asyncio.to_thread(_zip_fb2_inplace, nonlocal_dest_path)
                nonlocal_safe_name = os.path.basename(nonlocal_dest_path)
                nonlocal_bytes = os.path.getsize(nonlocal_dest_path)
                nonlocal_fmt = "fb2.zip"
                yield _sse_event("log", _log_entry("ok", f"Stored as {nonlocal_safe_name} ({_format_size(nonlocal_bytes)})"))

            yield _sse_event("log", _log_entry("info", "Computing BLAKE2b…"))
            file_hash = await asyncio.to_thread(_blake2b_of_file, nonlocal_dest_path)
            yield _sse_event("log", _log_entry("ok", f"hash = {file_hash}"))

            yield _sse_event("log", _log_entry("info", "Checking registry for duplicates…"))
            existing = db.query(models.Book).filter(models.Book.id == file_hash).first()
            if existing:
                yield _sse_event("log", _log_entry("warn", "Hash collision in registry"))
                yield _sse_event("log", _log_entry("error", f"Duplicate: matches existing book id {file_hash[:12]}…"))
                shutil.rmtree(sdir, ignore_errors=True)
                yield _sse_event("done", {"existing": _book_to_admin_detail(existing, db)})
                return
            yield _sse_event("log", _log_entry("ok", "No duplicate found"))

            yield _sse_event("log", _log_entry("info", f"Detecting format → {nonlocal_fmt.upper()}"))

            yield _sse_event("log", _log_entry("info", "Running metadata extractor…"))
            metadata = await asyncio.to_thread(_extract_upload_metadata, nonlocal_dest_path, nonlocal_fmt)
            if metadata.get("title"):
                yield _sse_event("log", _log_entry("ok", f"Parsed {nonlocal_fmt} description block"))
            else:
                yield _sse_event("log", _log_entry("warn", "No title found — filename fallback will apply"))
                metadata["title"] = os.path.splitext(nonlocal_safe_name)[0].replace("-", " ").replace("_", " ")

            yield _sse_event("log", _log_entry("info", "Extracting cover image…"))
            cover_dest = os.path.join(sdir, "cover.jpg")
            cover_dims = await asyncio.to_thread(_extract_cover_to, nonlocal_dest_path, nonlocal_fmt, cover_dest)
            if cover_dims:
                w, h = cover_dims
                yield _sse_event("log", _log_entry("ok", f"Cover extracted ({w} × {h} JPEG)"))
                yield _sse_event("log", _log_entry("info", "Generating 300px thumbnail…"))
                yield _sse_event("log", _log_entry("ok", f"Thumbnail written to {os.path.basename(cover_dest)}"))
            else:
                yield _sse_event("log", _log_entry("warn", "Cover extraction failed — none saved"))

            with state._STAGING_LOCK:
                state._STAGING[staging_id] = {
                    "dir": sdir,
                    "filename": nonlocal_safe_name,
                    "hash": file_hash,
                    "size": nonlocal_bytes,
                    "format": nonlocal_fmt,
                    "metadata": metadata,
                    "cover_w": cover_dims[0] if cover_dims else None,
                    "cover_h": cover_dims[1] if cover_dims else None,
                    "expires_at": datetime.now(timezone.utc).timestamp() + _STAGING_TTL_S,
                    "owner_id": owner_id,
                }

            yield _sse_event("log", _log_entry("ok", "Ready for review"))

            payload = {
                "staging_id": staging_id,
                "hash": file_hash,
                "size": nonlocal_bytes,
                "format": nonlocal_fmt,
                "filename": nonlocal_safe_name,
                "cover_url": f"/api/admin/books/upload/{staging_id}/cover.jpg" if cover_dims else None,
                "extracted_metadata": metadata,
            }
            yield _sse_event("done", payload)
        except Exception as e:
            logging.exception("admin_upload_book stream failed")
            shutil.rmtree(sdir, ignore_errors=True)
            yield _sse_event("log", _log_entry("error", f"Internal error: {e}"))
            yield _sse_event("done", {"error": str(e)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get("/api/admin/books/upload/{staging_id}/cover.jpg")
def admin_staging_cover(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    rec = state._STAGING.get(staging_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Staging not found")
    cover = os.path.join(rec["dir"], "cover.jpg")
    if not os.path.exists(cover):
        raise HTTPException(status_code=404, detail="No cover for staging")
    return FileResponse(cover, media_type="image/jpeg")


@router.post("/api/admin/books/upload/{staging_id}/cover", response_model=schemas.CoverUpdateResponse)
async def admin_staging_cover_override(
    staging_id: str,
    file: UploadFile = File(...),
    _admin: models.User = Depends(require_admin),
):
    rec = state._STAGING.get(staging_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Staging not found")
    _validate_cover_upload(file)
    raw = file.file.read(_MAX_COVER_BYTES + 1)
    if len(raw) > _MAX_COVER_BYTES:
        raise HTTPException(status_code=413, detail="Cover too large")
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Unreadable image")
    w, h = im.size
    if w > 300:
        new_h = max(1, int(h * 300 / w))
        im = im.resize((300, new_h), Image.LANCZOS)
        w, h = im.size
    dest = os.path.join(rec["dir"], "cover.jpg")
    im.save(dest, format="JPEG", quality=85)
    rec["cover_w"], rec["cover_h"] = w, h
    return {"cover_url": f"/api/admin/books/upload/{staging_id}/cover.jpg?v={int(datetime.now(timezone.utc).timestamp())}"}


def _get_staging_file(staging_id: str) -> str:
    """Resolve a staging_id to the absolute path of the staged book file. Used
    by the embedded-viewer endpoints so the admin can preview before commit."""
    rec = state._STAGING.get(staging_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Staging not found")
    path = os.path.join(rec["dir"], rec["filename"])
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Staging file missing")
    return path


@router.get("/api/admin/books/upload/{staging_id}/file")
def admin_staging_file(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    """Serve the raw staged file for embedded preview (PDF, EPUB, image)."""
    return FileResponse(_get_staging_file(staging_id))


@router.get("/api/admin/books/upload/{staging_id}/fb2-content")
async def admin_staging_fb2_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    # No stored-extension gate: the admin may be relabelling a mislabeled file,
    # so we let the reader decide. A genuine FB2 parses; anything else 422s.
    file_path = _get_staging_file(staging_id)
    try:
        xml_bytes = _m()._read_fb2_bytes(file_path)
        return _m()._convert_fb2(xml_bytes)
    except HTTPException:
        raise
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    except Exception:
        raise HTTPException(status_code=422, detail="Not a valid FB2 file")


@router.get("/api/admin/books/upload/{staging_id}/md-content")
async def admin_staging_md_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    file_path = _get_staging_file(staging_id)
    text = _m()._read_text_file(file_path)
    inner = _text_inner_ext(file_path)
    if inner == ".txt":
        return _m()._convert_txt(text)
    elif inner in CODE_EXTENSIONS:
        return _m()._convert_code(text, inner[1:])
    return _m()._convert_md(text)


@router.get("/api/admin/books/upload/{staging_id}/html-content")
async def admin_staging_html_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    # No stored-extension gate (see fb2-content): let the reader decide.
    file_path = _get_staging_file(staging_id)
    try:
        data = _m()._read_html_bytes(file_path)
        return _m()._convert_html(data)
    except HTTPException:
        raise
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    except Exception:
        raise HTTPException(status_code=422, detail="Not a valid HTML file")


@router.get("/api/admin/books/upload/{staging_id}/djvu-metadata")
async def admin_staging_djvu_metadata(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    # No stored-extension gate (see fb2-content). The decoder is lenient (it
    # happily reports 1 page for a non-DjVu file), so gate on the DjVu magic —
    # the same probe the commit step uses — to keep preview and commit in sync.
    file_path = _get_staging_file(staging_id)
    if _staged_reads_as(file_path, ".djvu") is not True:
        raise HTTPException(status_code=422, detail="Not a valid DjVu file")
    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()
        return {"total_pages": len(doc.pages)}
    except Exception:
        raise HTTPException(status_code=422, detail="Not a valid DjVu file")


@router.get("/api/admin/books/upload/{staging_id}/djvu-outline")
async def admin_staging_djvu_outline(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    # No stored-extension gate (see fb2-content); gate on the DjVu magic.
    file_path = _get_staging_file(staging_id)
    if _staged_reads_as(file_path, ".djvu") is not True:
        raise HTTPException(status_code=422, detail="Not a valid DjVu file")
    try:
        return {"toc": _m().extract_djvu_outline(file_path)}
    except Exception:
        raise HTTPException(status_code=422, detail="Not a valid DjVu file")


@router.get("/api/admin/books/upload/{staging_id}/djvu-page")
async def admin_staging_djvu_page(
    staging_id: str,
    page: int,
    _admin: models.User = Depends(require_admin),
):
    # No stored-extension gate (see fb2-content); gate on the DjVu magic.
    file_path = _get_staging_file(staging_id)
    if _staged_reads_as(file_path, ".djvu") is not True:
        raise HTTPException(status_code=422, detail="Not a valid DjVu file")
    if page < 1:
        raise HTTPException(status_code=400, detail="Invalid page number")
    headers = {"Cache-Control": "no-store"}
    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()
        if page > len(doc.pages):
            raise HTTPException(status_code=404, detail="Page not found")
        dpage = doc.pages[page - 1]
        job = dpage.decode(wait=True)
        width, height = job.width, job.height
        rect = (0, 0, width, height)
        fmt = djvu.decode.PixelFormatRgb()
        fmt.rows_top_to_bottom = True
        try:
            pixels = job.render(djvu.decode.RENDER_COLOR, rect, rect, fmt)
            img = Image.frombuffer('RGB', (width, height), pixels, 'raw', 'RGB', 0, 1)
        except djvu.decode.NotAvailable:
            img = Image.new('RGB', (width, height), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return Response(content=buf.getvalue(), media_type="image/jpeg", headers=headers)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=422, detail="Not a valid DjVu file")


@router.delete("/api/admin/books/upload/{staging_id}")
async def admin_cancel_staging(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    rec = state._STAGING.pop(staging_id, None)
    if rec:
        shutil.rmtree(rec.get("dir", ""), ignore_errors=True)
    return {"cancelled": staging_id}


@router.post("/api/admin/books/upload/touch")
async def admin_touch_staging(
    payload: schemas.TouchStagingRequest,
    admin: models.User = Depends(require_admin),
):
    """Keepalive: push out the TTL on the specific staged uploads the caller still
    has open, so a long multi-book review/edit session doesn't expire mid-way. We
    refresh ONLY the requested ids (still scoped to the owner) — refreshing all of
    the admin's records would let one open tab keep unrelated abandoned uploads
    alive elsewhere, defeating the TTL. Closing the page stops the pings, so
    abandoned uploads still expire within one TTL window."""
    now = datetime.now(timezone.utc).timestamp()
    wanted = set(payload.staging_ids)
    touched = 0
    with state._STAGING_LOCK:
        for sid, rec in state._STAGING.items():
            if sid in wanted and rec.get("owner_id") == admin.id:
                rec["expires_at"] = now + _STAGING_TTL_S
                touched += 1
    return {"touched": touched}


def _is_importable_file(abs_path: str) -> bool:
    """A plain (non-symlink) regular file with an accepted book extension. Symlinks
    are committed books already in the vault, so they're not importable."""
    if os.path.islink(abs_path) or not os.path.isfile(abs_path):
        return False
    return _detect_format(os.path.basename(abs_path)).split(".")[-1] in _ACCEPTED_BOOK_EXTS


_IMPORTABLE_CAP = 200


@router.post("/api/admin/books/importable")
async def admin_importable(
    payload: schemas.ImportableRequest,
    _admin: models.User = Depends(require_admin),
):
    """Expand a Browse selection (files and/or directories) into the concrete list
    of importable book files — plain (non-symlink) files with an accepted extension,
    recursively for directories. Lets the Browse 'Import to library' action turn a
    folder tick into file paths."""
    base = os.path.abspath(_m().BOOKS_DIR)
    seen: set[str] = set()
    found: list[str] = []

    def _add(abs_fp: str) -> None:
        if _is_importable_file(abs_fp):
            rel = os.path.relpath(abs_fp, base).replace("\\", "/")
            if rel not in seen:
                seen.add(rel)
                found.append(rel)

    for raw in payload.paths:
        try:
            abs_p = _safe_under_books(raw)
        except HTTPException:
            continue
        if not os.path.exists(abs_p):
            continue
        if os.path.isdir(abs_p) and not os.path.islink(abs_p):
            for root, dirs, files in os.walk(abs_p, followlinks=False):
                dirs[:] = [d for d in dirs if d not in _TOPDIR_SKIPLIST]
                for name in files:
                    _add(os.path.join(root, name))
        else:
            _add(abs_p)

    found.sort()
    return {"files": found[:_IMPORTABLE_CAP], "truncated": len(found) > _IMPORTABLE_CAP}


@router.post("/api/admin/books/stage-from-path")
async def admin_stage_from_path(
    payload: schemas.StageFromPathRequest,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Stage a file already on the server (e.g. under /Books/Unsorted) for commit,
    producing the SAME staging record the multipart upload does so the commit/
    preview/cover flow works unchanged. COPIES the file into staging (commit later
    moves the copy into the vault), leaving the original in place."""
    _purge_expired_staging()
    src = _safe_under_books(payload.path)
    if os.path.islink(src):
        raise HTTPException(status_code=409, detail="Already in the library")
    if not os.path.isfile(src):
        raise HTTPException(status_code=404, detail="File not found")
    filename = os.path.basename(src)
    fmt = _detect_format(filename)
    if fmt.split(".")[-1] not in _ACCEPTED_BOOK_EXTS:
        raise HTTPException(status_code=415, detail=f"Unsupported format: {fmt}")
    if os.path.getsize(src) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    _ensure_writable_dir(_m().STAGING_DIR)
    staging_id = uuid.uuid4().hex
    sdir = os.path.join(_m().STAGING_DIR, staging_id)
    os.makedirs(sdir, exist_ok=True)
    work_path = os.path.join(sdir, filename)
    try:
        await asyncio.to_thread(shutil.copy2, src, work_path)

        cur_fmt = fmt
        if cur_fmt == "fb2":
            work_path = await asyncio.to_thread(_zip_fb2_inplace, work_path)
            filename = os.path.basename(work_path)
            cur_fmt = "fb2.zip"
        size = os.path.getsize(work_path)

        file_hash = await asyncio.to_thread(_blake2b_of_file, work_path)
        existing = db.query(models.Book).filter(models.Book.id == file_hash).first()
        if existing:
            shutil.rmtree(sdir, ignore_errors=True)
            return {"existing": _book_to_admin_detail(existing, db)}

        metadata = await asyncio.to_thread(_extract_upload_metadata, work_path, cur_fmt)
        if not metadata.get("title"):
            metadata["title"] = os.path.splitext(filename)[0].replace("-", " ").replace("_", " ")

        cover_dest = os.path.join(sdir, "cover.jpg")
        cover_dims = await asyncio.to_thread(_extract_cover_to, work_path, cur_fmt, cover_dest)

        with state._STAGING_LOCK:
            state._STAGING[staging_id] = {
                "dir": sdir,
                "filename": filename,
                "hash": file_hash,
                "size": size,
                "format": cur_fmt,
                "metadata": metadata,
                "cover_w": cover_dims[0] if cover_dims else None,
                "cover_h": cover_dims[1] if cover_dims else None,
                "expires_at": datetime.now(timezone.utc).timestamp() + _STAGING_TTL_S,
                "owner_id": admin.id,
                "source_path": src,
            }
        return {
            "staging_id": staging_id,
            "hash": file_hash,
            "size": size,
            "format": cur_fmt,
            "filename": filename,
            "cover_url": f"/api/admin/books/upload/{staging_id}/cover.jpg" if cover_dims else None,
            "extracted_metadata": metadata,
        }
    except HTTPException:
        shutil.rmtree(sdir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(sdir, ignore_errors=True)
        logging.exception("stage-from-path failed")
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")


@router.post("/api/admin/books/commit", response_model=schemas.AdminBookDetail)
async def admin_commit_book(
    payload: schemas.UploadCommitRequest,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Finalise a staged upload: move file into the CAS vault, link from
    /<top>/<sub>/<filename>, register Book + BookLocation, return AdminBookDetail."""
    _purge_expired_staging()
    rec = state._STAGING.get(payload.staging_id)
    if not rec:
        raise HTTPException(status_code=410, detail="Staging expired or unknown")

    # top_dir is allowed to be empty (root) so admins can create a brand-new
    # top-level category by setting top=/ and subpath=<NewCategory>.
    raw_top = (payload.top_dir or "").strip().strip("/")
    top_dir = _safe_path_segment(raw_top) if raw_top else ""
    subpath = _safe_subpath(payload.subpath)
    if payload.clearance < 0 or payload.clearance > 100:
        raise HTTPException(status_code=400, detail="Clearance must be between 0 and 100")

    file_hash = rec["hash"]
    staging_filename = rec["filename"]
    requested_name = (payload.filename or staging_filename).strip()
    filename = _safe_path_segment(requested_name)
    staging_book = os.path.join(rec["dir"], staging_filename)
    # Allow changing the extension only when the staged bytes genuinely parse as
    # the new format (verified by the same reader the viewer uses), so the stored
    # content always matches its suffix and the live readers keep working. For
    # formats we can't probe we fall back to the old lexical "must keep" lock.
    original_suffix = _effective_suffix(staging_filename)
    requested_suffix = _effective_suffix(filename)
    if requested_suffix != original_suffix:
        verdict = _staged_reads_as(staging_book, requested_suffix)
        if verdict is None:
            raise HTTPException(status_code=400,
                detail=f"Filename must keep extension {original_suffix}")
        if not verdict:
            raise HTTPException(status_code=400,
                detail=f"File does not appear to be a valid {requested_suffix.lstrip('.').upper()}")
    staging_cover = os.path.join(rec["dir"], "cover.jpg")

    # Destination paths — drop empty segments so "//" doesn't sneak in when
    # uploading directly under root.
    rel_parts = [seg for seg in (top_dir, subpath) if seg]
    rel_dir = "/".join(rel_parts)
    if _is_recommended_path(rel_dir):
        raise HTTPException(
            status_code=400,
            detail="Cannot upload into the Recommended directory; recommend a book from its page instead.",
        )
    rel_path = f"{rel_dir}/{filename}" if rel_dir else filename
    abs_target_dir = os.path.abspath(os.path.join(_m().BOOKS_DIR, rel_dir))
    abs_target = os.path.abspath(os.path.join(_m().BOOKS_DIR, rel_path))
    if not abs_target.startswith(os.path.abspath(_m().BOOKS_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Path escapes library root")

    # If a registered book *just* showed up at this hash via a concurrent
    # commit (unlikely, but cheap to check), bail out.
    if db.query(models.Book).filter(models.Book.id == file_hash).first():
        shutil.rmtree(rec["dir"], ignore_errors=True)
        state._STAGING.pop(payload.staging_id, None)
        raise HTTPException(status_code=409, detail="Duplicate hash already registered")

    if os.path.lexists(abs_target):
        raise HTTPException(status_code=409, detail=f"A file already exists at {rel_path}")

    # 1. Move the staged file into the vault.
    vault_path = os.path.join(_m().DATA_DIR, file_hash)
    try:
        if not os.path.exists(vault_path):
            os.replace(staging_book, vault_path)
        else:
            # Vault file already exists (e.g. from a deleted-but-not-purged earlier).
            os.remove(staging_book)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Vault write failed: {e}")

    # 2. Move the cover into place, if any.
    if os.path.exists(staging_cover):
        _ensure_writable_dir(_m().COVERS_DIR)
        cover_dest = os.path.join(_m().COVERS_DIR, f"{file_hash}.jpg")
        try:
            os.replace(staging_cover, cover_dest)
        except OSError as e:
            logging.warning("cover move failed: %s", e)

    # 3. Create the symlink (relative target so the CAS layout stays portable).
    try:
        os.makedirs(abs_target_dir, exist_ok=True)
        rel_vault_target = os.path.relpath(vault_path, abs_target_dir)
        os.symlink(rel_vault_target, abs_target)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Symlink failed: {e}")

    # 4. Register in DB.
    meta = payload.metadata.model_dump(exclude_unset=True)
    title = meta.get("title") or os.path.splitext(filename)[0]
    try:
        vault_size = os.path.getsize(vault_path)
    except OSError:
        vault_size = None  # backfill_sizes.py will pick it up
    # Intrinsic media facts derived server-side from the vault bytes (never from the
    # client payload). NULL for non-A/V or if ffprobe can't read it; backfill_durations.py
    # is the catch-all.
    media_duration = media_bitrate = None
    file_ext = os.path.splitext(filename)[1].lstrip(".").lower()
    if file_ext in _AUDIO_EXTS or file_ext in _VIDEO_EXTS:
        # ffprobe is a blocking subprocess; run it off the event loop so a slow or
        # large file doesn't stall other requests for the duration of the probe.
        media_duration, media_bitrate = await asyncio.to_thread(_extract_media_meta, vault_path)
    book = models.Book(
        id=file_hash,
        title=title,
        author=meta.get("author"),
        publisher=meta.get("publisher"),
        published=meta.get("published"),
        description=meta.get("description"),
        tags=meta.get("tags"),
        series=meta.get("series"),
        languages=meta.get("languages"),
        identifiers=meta.get("identifiers"),
        original_filename=filename,
        needs_review=bool(payload.needs_review),
        clearance=int(payload.clearance),
        import_date=_now_iso(),
        size=vault_size,
        duration=media_duration,
        bitrate=media_bitrate,
    )
    db.add(book)
    db.add(models.BookLocation(hash_id=file_hash, symlink_path=rel_path))
    if payload.batch_id:
        _audit_batch_upload(db, admin, payload.batch_id,
                            {"title": title, "path": rel_path, "hash": file_hash, "clearance": int(payload.clearance)},
                            rel_dir)
    else:
        _audit(db, admin, "book.upload",
               target_kind="book", target_id=file_hash,
               summary=f'Uploaded "{title}" to /{rel_path}',
               details={"title": title, "path": rel_path, "size": vault_size, "clearance": int(payload.clearance)})
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        # Best-effort filesystem rollback: remove the symlink we just created.
        try:
            if os.path.islink(abs_target):
                os.remove(abs_target)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"DB commit failed: {e}")
    db.refresh(book)

    # 5. Cleanup staging.
    shutil.rmtree(rec["dir"], ignore_errors=True)
    with state._STAGING_LOCK:
        state._STAGING.pop(payload.staging_id, None)

    return _book_to_admin_detail(book, db)
