"""Router module (extracted from main.py): admin book-management endpoints —
per-book and bulk clearance, recommend/unrecommend (and their bulk variants),
the admin book-detail read/update/delete, the move (file + directory subtree)
endpoint, directory delete/list, and cover replace/re-extract. Includes the
recommendation symlink helpers and the cover-save helpers. Moved verbatim from
main.py (no logic change)."""
import os
import io
import re
import uuid
import shutil
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import or_
from PIL import Image

import models
import schemas
from database import get_db
from config import (
    RECOMMENDED_SUBDIR, _TOPDIR_SKIPLIST, _MAX_COVER_BYTES, _is_recommended_path,
    _detect_format, _effective_suffix, _now_iso,
)
from deps import require_admin
from paths import _first_book_path, _primary_topic_path, _safe_subpath, _assert_mutation_path
from cas import _extract_cover_to, _validate_cover_upload, _ensure_writable_dir
from background import _audit, _record_usage_event
from serialize import _cover_url_for, _book_to_admin_detail

router = APIRouter()


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect the
    library root via `monkeypatch.setattr(main, "BOOKS_DIR", ...)` (likewise
    DATA_DIR / RECOMMENDED_DIR / COVERS_DIR / STAGING_DIR), so these mutable
    runtime paths must be read through `main` (the patch target) rather than
    bound at import. main re-exports them from config, so production reads the
    same config values. Call-time import: no load-time cycle."""
    import main
    return main


@router.put("/api/admin/books/{hash_id}/clearance")
async def admin_set_book_clearance(
    hash_id: str,
    payload: schemas.BookClearanceUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.clearance < 0:
        raise HTTPException(status_code=400, detail="Clearance must be non-negative")
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    old = book.clearance
    if old != payload.clearance:
        _audit(db, admin, "book.clearance",
               target_kind="book", target_id=hash_id,
               summary=f'Set clearance of "{book.title or hash_id[:12]}" to {payload.clearance}',
               details={"title": book.title or hash_id[:12],
                        "path": _first_book_path(db, hash_id),
                        "old": old, "new": payload.clearance})
    book.clearance = payload.clearance
    db.commit()
    return {"hash_id": hash_id, "clearance": book.clearance}


@router.post("/api/admin/books/clearance")
async def admin_bulk_set_book_clearance(
    payload: schemas.BulkBookClearanceUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.clearance < 0:
        raise HTTPException(status_code=400, detail="Clearance must be non-negative")
    # Merge explicit hash_ids with the books beneath any selected directories.
    hash_ids = list(dict.fromkeys((payload.hash_ids or []) + _expand_dirs_to_hash_ids(db, payload.paths)))
    if not hash_ids:
        return {"updated": 0, "clearance": payload.clearance}
    # SQLite's UPDATE returns rows-matched, not rows-changed, so a no-op
    # bulk (every book already at the target clearance) would otherwise emit
    # an audit row claiming a mass change. Restrict the UPDATE to the hashes
    # whose value actually differs.
    changing_ids = [
        h for (h,) in db.query(models.Book.id)
            .filter(
                models.Book.id.in_(hash_ids),
                models.Book.clearance != payload.clearance,
            )
            .all()
    ]
    if not changing_ids:
        return {"updated": 0, "clearance": payload.clearance}
    updated = db.query(models.Book).filter(models.Book.id.in_(changing_ids)).update(
        {models.Book.clearance: payload.clearance},
        synchronize_session=False,
    )
    _audit(db, admin, "book.clearance",
           target_kind="book", target_id=None,
           summary=f"Set clearance of {updated} books to {payload.clearance}",
           details={"bulk": True, "count": updated, "new": payload.clearance,
                    "hash_ids": changing_ids[:25]})
    db.commit()
    return {"updated": updated, "clearance": payload.clearance}


def _sanitize_for_fs(name: str, max_len: int = 200) -> str:
    """Sanitize a string for use as a single path component. Replaces path
    separators and control chars with spaces, collapses runs of whitespace,
    strips leading/trailing dots (so the result can't be a hidden file or '.'
    / '..'). Returns the empty string when nothing usable is left — callers
    fall back to original_filename in that case."""
    cleaned = re.sub(r"[\\/\x00-\x1f\x7f]+", " ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:max_len]


def _recommended_basename(book: models.Book) -> str:
    """Filename to use for the symlink under RECOMMENDED_DIR. Prefer a
    sanitized version of the book title + the format suffix carried by
    original_filename; fall back to a sanitized original_filename when the
    title is empty, and to the truncated hash when even that yields nothing
    after sanitization. Every component goes through `_sanitize_for_fs`
    so `os.path.join(RECOMMENDED_DIR, ...)` cannot escape RECOMMENDED_DIR
    via `../` segments in user-controlled metadata.

    The suffix comes from `_effective_suffix`, which treats compound formats
    (`.fb2.zip`, `.txt.zip`, …) as a single unit, so a book uploaded as
    `something.fb2.zip` is recommended as `<Title>.fb2.zip` rather than the
    lossy `<Title>.zip` (a `.zip` symlink mis-reads as ZIP everywhere format
    is inferred from the filename, and downloads lose the real extension)."""
    title = _sanitize_for_fs(book.title or "")
    suf = _effective_suffix(book.original_filename or "")   # '', '.pdf', '.fb2.zip', ...
    # Sanitize each dot-component but keep the dots that make up a compound
    # suffix (.fb2.zip stays .fb2.zip; control chars in any piece are scrubbed).
    ext = "".join("." + _sanitize_for_fs(p) for p in suf.split(".") if p) if suf else ""
    if title:
        return f"{title}{ext}"
    fallback = _sanitize_for_fs(book.original_filename or "")
    return fallback or book.id[:12]


def _find_free_recommended_name(base: str) -> str:
    """Append `-2`, `-3`, … to the stem until the name is free under
    RECOMMENDED_DIR. Uses os.path.lexists so broken symlinks still count as
    collisions."""
    if not os.path.lexists(os.path.join(_m().RECOMMENDED_DIR, base)):
        return base
    if "." in base:
        stem, _, ext = base.rpartition(".")
        ext = "." + ext
    else:
        stem, ext = base, ""
    i = 2
    while True:
        candidate = f"{stem}-{i}{ext}"
        if not os.path.lexists(os.path.join(_m().RECOMMENDED_DIR, candidate)):
            return candidate
        i += 1


def _create_recommendation(db: Session, admin: models.User, book: models.Book) -> str:
    """Materialise a recommendation: create the symlink in RECOMMENDED_DIR,
    add a book_locations row, insert a book_recommendations row. Returns the
    chosen relative symlink_path (e.g. ``"Recommended/Voyna i mir.pdf"``).

    Does NOT commit — caller is responsible. Caller MUST also have verified
    the book is not already recommended; this helper does not re-check, so a
    second call for the same book leaves a duplicate symlink behind.
    """
    base = _recommended_basename(book)
    if not base:
        raise HTTPException(status_code=400, detail="Cannot derive a filename for recommendation")
    os.makedirs(_m().RECOMMENDED_DIR, exist_ok=True)
    name = _find_free_recommended_name(base)
    rel = f"Recommended/{name}"
    dst_abs = os.path.join(_m().RECOMMENDED_DIR, name)
    # Defense-in-depth: `_recommended_basename` already sanitises both branches,
    # but a future regression that lets `..` segments through must not be able
    # to land the symlink outside RECOMMENDED_DIR. Mirrors the abspath guard
    # used throughout main.py for BOOKS_DIR.
    if not os.path.abspath(dst_abs).startswith(os.path.abspath(_m().RECOMMENDED_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Cannot derive a filename for recommendation")
    # abspath() is lexical — refuse to create through a symlinked Recommended/.
    _assert_mutation_path(dst_abs)
    vault_path = os.path.join(_m().DATA_DIR, book.id)
    target = os.path.relpath(vault_path, _m().RECOMMENDED_DIR)
    try:
        os.symlink(target, dst_abs)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Recommended path collided mid-flight")
    try:
        db.add(models.BookLocation(hash_id=book.id, symlink_path=rel))
        db.add(models.BookRecommendation(
            hash_id=book.id,
            recommended_by=admin.id,
            recommended_at=_now_iso(),
        ))
    except Exception:
        try:
            os.unlink(dst_abs)
        except OSError:
            pass
        raise
    return rel


def _remove_recommendation(db: Session, hash_id: str) -> tuple[list[str], bool]:
    """Tear down a recommendation: unlink every Recommended/ symlink for this
    hash (FileNotFoundError tolerated), delete those book_locations rows, and
    drop the book_recommendations row. Returns (removed_symlink_paths,
    had_recommendation_row). Does NOT commit — caller is responsible."""
    rec_locs = db.query(models.BookLocation).filter(
        models.BookLocation.hash_id == hash_id,
        models.BookLocation.symlink_path.like("Recommended/%"),
    ).all()
    removed: list[str] = []
    all_unlinked = True
    for loc in rec_locs:
        abs_path = os.path.join(_m().BOOKS_DIR, loc.symlink_path)
        try:
            _assert_mutation_path(abs_path)     # no unlink through a symlinked dir chain
        except HTTPException:
            logging.warning("unrecommend: refused traversal: %s", loc.symlink_path)
            all_unlinked = False
            continue
        try:
            os.unlink(abs_path)
        except FileNotFoundError:
            pass                                # already gone — desired post-state achieved
        except OSError as e:
            # Real unlink failure (PermissionError, ReadOnly FS, …). Leave the
            # book_locations row in place so a retry can complete the operation
            # instead of falsely reporting `removed: [...]` while the symlink
            # lingers on disk.
            logging.warning("unrecommend: failed to unlink %s: %s", loc.symlink_path, e)
            all_unlinked = False
            continue
        db.delete(loc)
        removed.append(loc.symlink_path)
    # Only drop the recommendation row when every symlink for this hash was
    # actually removed. If any unlink failed, keep the row so the book is
    # still considered "recommended" (and its remaining symlink + book_location
    # still match), letting the operator retry without falling into an orphan
    # state where the file is on disk with no DB row pointing at it.
    deleted_rec = 0
    if all_unlinked:
        deleted_rec = db.query(models.BookRecommendation).filter(
            models.BookRecommendation.hash_id == hash_id
        ).delete(synchronize_session=False)
    return removed, bool(deleted_rec)


@router.post("/api/admin/books/{hash_id}/recommend", response_model=schemas.RecommendationResponse)
async def admin_recommend_book(
    request: Request,
    hash_id: str,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Mark a book as recommended. Creates a symlink under
    /Books/Recommended/ named from the book's title (or original_filename when
    title is empty), registers the new book_locations row, and stores
    who/when in book_recommendations. Idempotent: re-recommending an already-
    recommended book is a no-op that returns the existing record."""
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    rec = db.query(models.BookRecommendation).filter(
        models.BookRecommendation.hash_id == hash_id
    ).first()
    topic_path = _primary_topic_path(db, hash_id)
    new_symlink: Optional[str] = None
    if rec is None:
        new_symlink = _create_recommendation(db, admin, book)
        _audit(db, admin, "book.recommend",
               target_kind="book", target_id=hash_id,
               summary=f'Recommended "{book.title or book.original_filename}"',
               details={"title": book.title or book.original_filename,
                        "path": topic_path,
                        "symlink_path": new_symlink})
        # Symlink is already on disk; if the commit fails the DB rolls back but
        # the symlink would orphan unless we tear it down too.
        try:
            db.commit()
        except Exception:
            try:
                os.unlink(os.path.join(_m().BOOKS_DIR, new_symlink))
            except OSError:
                pass
            raise
        rec = db.query(models.BookRecommendation).filter(
            models.BookRecommendation.hash_id == hash_id
        ).first()
        if rec is None:
            # Race: a concurrent DELETE /recommend landed between our commit
            # and this re-read, removing the row (and its book_locations +
            # on-disk symlink) we just wrote. Surface as 409 so the client
            # retries from fresh state rather than getting a 500 on
            # rec.recommended_by below.
            raise HTTPException(status_code=409, detail="Recommendation state changed mid-request")
    if new_symlink:
        _record_usage_event(request, "recommend", user=admin,
                            hash_id=hash_id,
                            path=topic_path,
                            extra={"title": book.title or book.original_filename,
                                   "symlink_path": new_symlink})
    rec_user = db.query(models.User).filter(models.User.id == rec.recommended_by).first()
    return schemas.RecommendationResponse(
        hash_id=hash_id,
        recommended_by=rec.recommended_by,
        recommended_by_name=rec_user.real_name if rec_user else None,
        recommended_at=rec.recommended_at,
        symlink_path=new_symlink,
    )


@router.delete("/api/admin/books/{hash_id}/recommend", response_model=schemas.UnrecommendResponse)
async def admin_unrecommend_book(
    request: Request,
    hash_id: str,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove a book from the Recommended set. Deletes every book_locations
    row whose path is under Recommended/ for this hash, unlinks each symlink
    on disk (FileNotFoundError ignored), and drops the book_recommendations
    row. Idempotent: returns ok with removed=[] when the book wasn't
    recommended in the first place."""
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    # Snapshot a real topic path before we tear down the Recommended/ entries,
    # so the usage event's Path column points at the book's actual location.
    topic_path = _primary_topic_path(db, hash_id)
    removed, had_rec = _remove_recommendation(db, hash_id)
    changed = bool(removed) or had_rec
    if changed:
        _audit(db, admin, "book.unrecommend",
               target_kind="book", target_id=hash_id,
               summary=f'Removed recommendation for "{book.title or book.original_filename}"',
               details={"title": book.title or book.original_filename,
                        "path": topic_path,
                        "removed": removed})
    db.commit()
    if changed:
        _record_usage_event(request, "unrecommend", user=admin,
                            hash_id=hash_id,
                            path=topic_path,
                            extra={"title": book.title or book.original_filename})
    return {"ok": True, "removed": removed}


@router.post("/api/admin/books/recommend/bulk", response_model=schemas.BulkRecommendResponse)
async def admin_recommend_bulk(
    request: Request,
    payload: schemas.BulkRecommendRequest,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Bulk-recommend the listed books. Idempotent per book: already-
    recommended hashes are silently counted as `unchanged`. One audit row +
    one usage event per newly-recommended hash, so the per-book timeline
    filters at /api/admin/usage and /api/me/activity surface each action."""
    out = schemas.BulkRecommendResponse()
    # Merge explicit hash_ids with the books beneath any selected directories.
    # Dedup while preserving order — a client posting ["abc", "abc"] would
    # otherwise pass the `already` pre-check twice and produce duplicate
    # symlinks (Recommended/X.pdf, Recommended/X-2.pdf) plus a PK conflict at
    # commit. Frontend currently dedups, but the API contract makes no such
    # guarantee.
    hash_ids = list(dict.fromkeys((payload.hash_ids or []) + _expand_dirs_to_hash_ids(db, payload.paths)))
    if not hash_ids:
        return out
    books = {b.id: b for b in db.query(models.Book).filter(
        models.Book.id.in_(hash_ids)
    ).all()}
    already = {
        r[0] for r in db.query(models.BookRecommendation.hash_id).filter(
            models.BookRecommendation.hash_id.in_(hash_ids)
        ).all()
    }
    newly: list[tuple[str, str, str]] = []   # (hash_id, symlink_path, topic_path)
    for hid in hash_ids:
        book = books.get(hid)
        if not book:
            out.errors.append({"hash_id": hid, "reason": "not found"})
            continue
        if hid in already:
            out.unchanged += 1
            continue
        try:
            topic_path = _primary_topic_path(db, hid)
            symlink_path = _create_recommendation(db, admin, book)
            newly.append((hid, symlink_path, topic_path))
            out.recommended += 1
        except HTTPException as e:
            out.errors.append({"hash_id": hid, "reason": str(e.detail)})
        except Exception as e:
            out.errors.append({"hash_id": hid, "reason": f"unexpected: {e}"})
    if newly:
        _audit(db, admin, "book.recommend",
               target_kind="book", target_id=None,
               summary=f"Recommended {len(newly)} books",
               details={"bulk": True, "count": len(newly),
                        "hash_ids": [h for h, _s, _p in newly][:25]})
    # Every entry in `newly` already has its symlink on disk; if the commit
    # fails the DB rolls back but those symlinks would orphan unless we tear
    # them down too.
    try:
        db.commit()
    except Exception:
        for _hid, sym, _tp in newly:
            try:
                os.unlink(os.path.join(_m().BOOKS_DIR, sym))
            except OSError:
                pass
        raise
    for hid, symlink_path, topic_path in newly:
        book = books[hid]
        _record_usage_event(request, "recommend", user=admin,
                            hash_id=hid,
                            path=topic_path,
                            extra={"bulk": True,
                                   "title": book.title or book.original_filename,
                                   "symlink_path": symlink_path})
    return out


@router.post("/api/admin/books/unrecommend/bulk", response_model=schemas.BulkUnrecommendResponse)
async def admin_unrecommend_bulk(
    request: Request,
    payload: schemas.BulkRecommendRequest,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Bulk-unrecommend the listed books — the "select all inside Recommended/
    and remove them" workflow. Idempotent per book: hashes that weren't
    recommended are counted as `unchanged`. One audit row + one usage event
    per affected hash, so per-book timeline filters surface each action."""
    out = schemas.BulkUnrecommendResponse()
    # Merge explicit hash_ids with the books beneath any selected directories.
    # Dedup while preserving order — see admin_recommend_bulk for why.
    hash_ids = list(dict.fromkeys((payload.hash_ids or []) + _expand_dirs_to_hash_ids(db, payload.paths)))
    if not hash_ids:
        return out
    affected: list[tuple[str, str]] = []   # (hash_id, topic_path)
    for hid in hash_ids:
        try:
            topic_path = _primary_topic_path(db, hid)
            removed, had_rec = _remove_recommendation(db, hid)
            if removed or had_rec:
                affected.append((hid, topic_path))
                out.unrecommended += 1
            else:
                out.unchanged += 1
        except Exception as e:
            out.errors.append({"hash_id": hid, "reason": f"unexpected: {e}"})
    if affected:
        _audit(db, admin, "book.unrecommend",
               target_kind="book", target_id=None,
               summary=f"Removed recommendation for {len(affected)} books",
               details={"bulk": True, "count": len(affected),
                        "hash_ids": [h for h, _p in affected][:25]})
    db.commit()
    titles = {b.id: (b.title or b.original_filename)
              for b in db.query(models.Book).filter(
                  models.Book.id.in_([h for h, _p in affected])
              ).all()} if affected else {}
    for hid, topic_path in affected:
        _record_usage_event(request, "unrecommend", user=admin,
                            hash_id=hid,
                            path=topic_path,
                            extra={"bulk": True,
                                   "title": titles.get(hid)})
    return out


@router.get("/api/admin/books/{hash_id}", response_model=schemas.AdminBookDetail)
async def admin_get_book(
    hash_id: str,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return _book_to_admin_detail(book, db)


@router.put("/api/admin/books/{hash_id}", response_model=schemas.AdminBookDetail)
async def admin_update_book(
    hash_id: str,
    payload: schemas.BookUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    updates = payload.model_dump(exclude_unset=True)
    if "clearance" in updates and updates["clearance"] is not None and updates["clearance"] < 0:
        raise HTTPException(status_code=400, detail="Clearance must be non-negative")
    # Capture the title BEFORE the mutation loop — otherwise a rename ("Foo"
    # → "Bar") produces an audit summary like 'Edited "Bar": title', which
    # reads as if Bar were the original.
    original_title = book.title or hash_id[:12]
    diff: dict[str, list] = {}
    for field, val in updates.items():
        old = getattr(book, field)
        if old != val:
            diff[field] = [old, val]
        setattr(book, field, val)
    if diff:
        _audit(db, admin, "book.edit",
               target_kind="book", target_id=hash_id,
               summary=f'Edited "{original_title}": {", ".join(diff.keys())}',
               details={"title": original_title, "path": _first_book_path(db, hash_id),
                        "changed": diff})
    db.commit()
    db.refresh(book)
    return _book_to_admin_detail(book, db)


@router.delete("/api/admin/books/{hash_id}")
async def admin_delete_book(
    hash_id: str,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    deleted_title = book.title or hash_id[:12]

    locations = [r[0] for r in db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == hash_id
    ).all()]

    # Symlink cleanup first — symlinks are recoverable (recreatable from the
    # still-committed book_locations rows) if the DB commit below fails, so
    # they may go before it. Tolerate missing files — best-effort.
    errors = []
    books_root = os.path.abspath(_m().BOOKS_DIR)
    for sp in locations:
        full = os.path.abspath(os.path.join(_m().BOOKS_DIR, sp))
        # Refuse to touch anything that escapes BOOKS_DIR (lexically or through
        # a symlinked directory component) or that isn't a symlink.
        if not full.startswith(books_root + os.sep):
            errors.append(f"refused traversal: {sp}")
            continue
        try:
            _assert_mutation_path(full)
        except HTTPException:
            errors.append(f"refused traversal: {sp}")
            continue
        if os.path.islink(full):
            try:
                os.remove(full)
            except OSError as e:
                errors.append(f"symlink {sp}: {e}")

    # FK enforcement is ON (database.py), so deleting the book row cascades its
    # child rows (book_locations, favorites, reading_progress, book_ratings,
    # book_comments + replies, book_recommendations, playlist_items, annotations)
    # and SET-NULLs feedback_threads.book_hash_id.
    db.delete(book)
    _audit(db, admin, "book.delete",
           target_kind="book", target_id=hash_id,
           summary=f'Deleted "{deleted_title}"',
           details={"title": deleted_title,
                    "path": locations[0] if locations else None,
                    "locations": locations})
    db.commit()

    # Only now — past the point of no rollback — remove the vault bytes and
    # cover. These are the unrecoverable artifacts: doing it before the commit
    # would leave a books row whose content is gone if the commit failed
    # (same ordering as admin_delete_dir).
    vault_file = os.path.join(_m().BOOKS_DIR, ".data", hash_id)
    if os.path.exists(vault_file):
        try:
            os.remove(vault_file)
        except OSError as e:
            errors.append(f"vault: {e}")
    cover_file = os.path.join(_m().BOOKS_DIR, ".data", "covers", f"{hash_id}.jpg")
    if os.path.exists(cover_file):
        try:
            os.remove(cover_file)
        except OSError as e:
            errors.append(f"cover: {e}")

    return {"deleted": hash_id, "locations": locations, "errors": errors}


# ---------- Admin: move book(s) between locations ----------


def _rel_under_books(p: str) -> str:
    """Normalise an input path to a relative POSIX path under BOOKS_DIR.
    Strips leading/trailing slashes, rejects empty + traversal. Mirrors the
    inline guard used by the commit endpoint at main.py:1496-1498."""
    rel = (p or "").strip().lstrip("/").rstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="Empty path")
    abs_p = os.path.abspath(os.path.join(_m().BOOKS_DIR, rel))
    books_abs = os.path.abspath(_m().BOOKS_DIR)
    if not (abs_p == books_abs or abs_p.startswith(books_abs + os.sep)):
        raise HTTPException(status_code=400, detail="Path escapes library root")
    return os.path.relpath(abs_p, books_abs).replace("\\", "/")


def _expand_dirs_to_hash_ids(db: Session, paths: list[str] | None) -> list[str]:
    """Expand Browse directory selections into the hash_ids of every registered
    book in their subtrees (recursively), order-preserving and de-duped. Mirrors
    the prefix-scan in admin_move. Each path goes through the _rel_under_books
    traversal guard; a path that is itself a registered book location is included
    too. Admin-only callers — no clearance filtering (admins see everything)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in (paths or []):
        try:
            rel = _rel_under_books(raw)
        except HTTPException:
            continue
        rows = (
            db.query(models.BookLocation.hash_id)
            .filter(or_(
                models.BookLocation.symlink_path == rel,
                models.BookLocation.symlink_path.like(rel + "/%"),
            ))
            .all()
        )
        for (hid,) in rows:
            if hid not in seen:
                seen.add(hid)
                out.append(hid)
    return out


def _rmdir_empty_upwards(start_abs: str) -> None:
    """rmdir start_abs and every empty parent directory up to (but not
    including) BOOKS_DIR. Tolerant — stops at first non-empty dir or OSError."""
    books_abs = os.path.abspath(_m().BOOKS_DIR)
    cur = os.path.abspath(start_abs)
    while cur.startswith(books_abs + os.sep) and cur != books_abs:
        try:
            os.rmdir(cur)
        except OSError:
            return
        cur = os.path.dirname(cur)


@router.post("/api/admin/move", response_model=schemas.MoveResponse)
async def admin_move(
    payload: schemas.MoveRequest,
    dry_run: bool = False,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Move a managed book (symlink) or an entire directory subtree to a new
    location. The hash never changes — only `book_locations.symlink_path` and
    the symlink on disk are updated. Favourites and reading progress reference
    by `hash_id`, so they survive the move automatically."""
    src = _rel_under_books(payload.src)
    dst = _rel_under_books(payload.dst)

    # The Recommended/ tree is managed only by the recommend/unrecommend
    # endpoints. Refuse to move books into it, out of it, or to relocate the
    # directory itself — use the (un)recommend action instead.
    if _is_recommended_path(src) or _is_recommended_path(dst):
        raise HTTPException(
            status_code=400,
            detail="The Recommended directory is managed automatically; use the recommend action instead of moving files.",
        )

    if src == dst:
        return schemas.MoveResponse(
            src=src, dst=dst, kind="file", dry_run=dry_run,
            moved=[], errors=[],
            skipped=[{"path": src, "reason": "from == to"}],
        )

    # A move that lands inside its own subtree (Urantia → Urantia/Foo) would
    # corrupt the prefix-rename invariant. Reject up front.
    if (dst + "/").startswith(src + "/"):
        raise HTTPException(status_code=400, detail="Cannot move into a subdirectory of itself")

    books_abs = os.path.abspath(_m().BOOKS_DIR)
    src_abs = os.path.join(books_abs, src)

    if not os.path.lexists(src_abs):
        raise HTTPException(status_code=404, detail="Source not found")
    # _rel_under_books is lexical — refuse a source whose directory chain runs
    # through a symlink (the post-move rmdir sweep would follow it). Parent
    # chain only: a file move's leaf is the book symlink itself.
    _assert_mutation_path(src_abs)

    # Decide file vs directory. "File" here means a symlink into the CAS vault
    # (i.e. a managed book). Anything else is rejected.
    if os.path.islink(src_abs):
        kind = "file"
        rows = (
            db.query(models.BookLocation)
            .filter(models.BookLocation.symlink_path == src)
            .all()
        )
        if not rows:
            raise HTTPException(status_code=400, detail="Not a managed book")
        moves: list[tuple[str, str, str]] = [(src, dst, rows[0].hash_id)]
    elif os.path.isdir(src_abs):
        kind = "directory"
        like_prefix = src + "/"
        rows = (
            db.query(models.BookLocation)
            .filter(models.BookLocation.symlink_path.like(like_prefix + "%"))
            .all()
        )
        if not rows:
            raise HTTPException(status_code=404, detail="No registered books under this directory")
        moves = [(r.symlink_path, dst + r.symlink_path[len(src):], r.hash_id) for r in rows]
    else:
        raise HTTPException(status_code=400, detail="Source is neither a symlink nor a directory")

    # Pre-flight: every destination must be free. All-or-nothing on collision.
    collisions = []
    for (_old, new_rel, _h) in moves:
        new_abs = os.path.join(books_abs, new_rel)
        if os.path.lexists(new_abs):
            collisions.append({"path": new_rel, "reason": "destination exists"})
    if collisions:
        return schemas.MoveResponse(
            src=src, dst=dst, kind=kind, dry_run=dry_run,
            moved=[], errors=collisions, skipped=[],
        )

    if dry_run:
        return schemas.MoveResponse(
            src=src, dst=dst, kind=kind, dry_run=True,
            moved=[schemas.MoveItem(src=o, dst=n, hash_id=h) for (o, n, h) in moves],
            errors=[], skipped=[],
        )

    moved: list[schemas.MoveItem] = []
    errors: list[dict] = []

    for (old_rel, new_rel, hash_id) in moves:
        old_abs = os.path.join(books_abs, old_rel)
        new_abs = os.path.join(books_abs, new_rel)
        new_parent = os.path.dirname(new_abs)

        # Both endpoints of the move must have symlink-free directory chains:
        # a symlinked component would redirect the makedirs/symlink/remove
        # below outside the tree (or into infra under it).
        try:
            _assert_mutation_path(new_abs)
            _assert_mutation_path(old_abs)
        except HTTPException:
            errors.append({"path": old_rel, "reason": "refused traversal"})
            continue

        # Re-derive a relative symlink target from the NEW parent — the old
        # symlink's relative target is invalid from a different parent.
        # Mirrors the commit endpoint's pattern (main.py:1534-1535).
        try:
            os.makedirs(new_parent, exist_ok=True)
            vault_path = os.path.join(_m().DATA_DIR, hash_id)
            new_target = os.path.relpath(vault_path, new_parent)
        except OSError as e:
            errors.append({"path": old_rel, "reason": f"mkdir failed: {e}"})
            continue

        # (a) create new symlink
        try:
            os.symlink(new_target, new_abs)
        except FileExistsError:
            errors.append({"path": new_rel, "reason": "race: destination appeared"})
            continue
        except OSError as e:
            errors.append({"path": old_rel, "reason": f"symlink failed: {e}"})
            continue

        # (b) update DB
        try:
            db.query(models.BookLocation).filter(
                models.BookLocation.symlink_path == old_rel
            ).update(
                {models.BookLocation.symlink_path: new_rel},
                synchronize_session=False,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            # (c) rollback step (a)
            try:
                os.remove(new_abs)
            except OSError:
                pass
            errors.append({"path": old_rel, "reason": f"DB update failed: {e}"})
            continue

        # (d) best-effort removal of the old symlink. If this fails the DB is
        # already authoritative — leaves a stale symlink behind that the user
        # can clean up later. Log for visibility.
        try:
            os.remove(old_abs)
        except OSError as ex:
            logging.warning("admin_move: stale old symlink left behind: %s (%s)", old_rel, ex)

        moved.append(schemas.MoveItem(src=old_rel, dst=new_rel, hash_id=hash_id))

    # For directory moves, sweep the now-likely-empty source subtree, then
    # walk up removing empty parents until we hit a non-empty dir or BOOKS_DIR.
    if kind == "directory" and os.path.isdir(src_abs):
        for root, _dirs, _files in os.walk(src_abs, topdown=False):
            try:
                os.rmdir(root)
            except OSError:
                pass
        _rmdir_empty_upwards(os.path.dirname(src_abs))

    # Audit gets its own commit — the per-book DB updates above each committed
    # independently, so there's no caller transaction left to ride.
    if moved:
        if kind == "file":
            m = moved[0]
            filename = m.src.rsplit("/", 1)[-1]
            summary = f'Moved "{filename}" from /{src} to /{dst}'
            target_id: str | None = m.hash_id
            # `path` snapshots the post-move symlink so the audit row links to
            # where the book lives now, not where it came from.
            move_details = {"filename": filename, "path": m.dst,
                            "src": src, "dst": dst,
                            "kind": kind, "count": len(moved)}
        else:
            summary = f"Moved {len(moved)} books from /{src}/ to /{dst}/"
            target_id = None
            move_details = {"src": src, "dst": dst, "kind": kind, "count": len(moved)}
        _audit(db, admin, "book.move",
               target_kind="book", target_id=target_id,
               summary=summary,
               details=move_details)
        db.commit()

    return schemas.MoveResponse(
        src=src, dst=dst, kind=kind, dry_run=False,
        moved=moved, errors=errors, skipped=[],
    )


# ---------- Admin upload (Add Book wizard) ----------


def _like_escape(s: str) -> str:
    """Escape SQL LIKE metacharacters so a literal path can be used as a
    prefix pattern. A directory named e.g. `Law_2024` must not match
    `LawX2024` via the `_` wildcard."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.delete("/api/admin/dirs")
def admin_delete_dir(
    path: str,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Recursively delete a directory, cleaning up symlinks, book database entries,
    and orphan vault/cover files.

    Destructive steps are ordered so nothing irreversible happens until success
    is guaranteed: the DB is queried read-only, then `rmtree` runs (its failure
    leaves the DB and vault untouched), then DB rows are deleted and committed,
    and only after the commit do orphaned vault/cover files get removed."""
    sub = _safe_subpath(path)
    if not sub:
        raise HTTPException(status_code=400, detail="Cannot delete root directory")
    if _is_recommended_path(sub):
        raise HTTPException(
            status_code=400,
            detail="The Recommended directory cannot be deleted. Remove individual books by unrecommending them.",
        )

    target_dir = os.path.abspath(os.path.join(_m().BOOKS_DIR, sub))
    books_root = os.path.abspath(_m().BOOKS_DIR)

    if not target_dir.startswith(books_root + os.sep) or target_dir == books_root:
        raise HTTPException(status_code=403, detail="Forbidden")

    if os.path.islink(target_dir):
        raise HTTPException(status_code=400, detail="Path is a symlink, not a directory")

    if not os.path.exists(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="Path is not a directory")

    # The islink() check above only inspects the leaf; a symlinked *intermediate*
    # component (Topic/escape/sub) would point rmtree at a tree outside BOOKS_DIR
    # (or into infra under it). Resolve the whole chain before deleting.
    _assert_mutation_path(target_dir, is_dir=True)

    # The DB — not the filesystem — is the canonical record of book locations.
    # One indexed prefix scan finds every location under the directory; the
    # exact `startswith` refine guards against LIKE wildcard over-matching.
    prefix = f"{sub}/"
    like_prefix = _like_escape(prefix) + "%"
    locs = [
        r for r in db.query(models.BookLocation)
        .filter(models.BookLocation.symlink_path.like(like_prefix, escape="\\"))
        .all()
        if r.symlink_path.startswith(prefix)
    ]

    # A hash is orphaned iff it has no location left outside the deleted subtree.
    inside_hashes = {r.hash_id for r in locs}
    orphan_hashes = []
    for h in inside_hashes:
        other = (
            db.query(models.BookLocation.symlink_path)
            .filter(models.BookLocation.hash_id == h)
            .all()
        )
        if all(sp[0].startswith(prefix) for sp in other):
            orphan_hashes.append(h)

    # rmtree first: on failure the DB and vault are still pristine.
    try:
        shutil.rmtree(target_dir)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove directory: {e}")

    # DB cleanup. A book that survives (keeps locations outside the subtree)
    # loses only its in-subtree location rows. A book that loses its last
    # location (orphan_hashes) is deleted outright — FK enforcement (database.py)
    # then cascades ALL its locations plus its per-user rows (favorites,
    # reading_progress, book_ratings, book_comments, playlist_items, annotations,
    # …) and SET-NULLs feedback_threads.book_hash_id. Deleting an orphan book's
    # locations explicitly too would race that cascade (a 0-row delete), so only
    # the surviving books' locations are removed here.
    orphan_set = set(orphan_hashes)
    for loc in locs:
        if loc.hash_id not in orphan_set:
            db.delete(loc)
    for h in orphan_hashes:
        book = db.query(models.Book).filter(models.Book.id == h).first()
        if book:
            db.delete(book)
    # Directory bookmarks AND directory playlist items for the deleted directory
    # and any subdirectory (directories aren't content-addressed, so no FK).
    db.query(models.DirectoryFavorite).filter(
        or_(
            models.DirectoryFavorite.path == sub,
            models.DirectoryFavorite.path.like(like_prefix, escape="\\"),
        )
    ).delete(synchronize_session=False)
    db.query(models.PlaylistItem).filter(
        models.PlaylistItem.item_type == "directory",
        or_(
            models.PlaylistItem.dir_path == sub,
            models.PlaylistItem.dir_path.like(like_prefix, escape="\\"),
        )
    ).delete(synchronize_session=False)
    db.commit()

    # Only now — past the point of no rollback — remove orphan vault/cover
    # files. Best-effort: failures are reported but don't fail the request.
    errors = []
    for h in orphan_hashes:
        vault_file = os.path.join(_m().BOOKS_DIR, ".data", h)
        if os.path.exists(vault_file):
            try:
                os.remove(vault_file)
            except OSError as e:
                errors.append(f"vault {h}: {e}")
        cover_file = os.path.join(_m().BOOKS_DIR, ".data", "covers", f"{h}.jpg")
        if os.path.exists(cover_file):
            try:
                os.remove(cover_file)
            except OSError as e:
                errors.append(f"cover {h}: {e}")

    return {
        "deleted_directory": sub,
        "locations_removed": len(locs),
        "books_deleted": len(orphan_hashes),
        "errors": errors,
    }


@router.get("/api/admin/dirs", response_model=schemas.DirListing)
def admin_dirs(
    path: str = "",
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List immediate subdirectory names under /<path>/ — powers the
    expand-on-click directory tree in the upload view. Combines DB-known
    children with on-disk directories so empty-but-existing folders appear
    too. `path=""` lists root-level directories."""
    sub = _safe_subpath(path)
    prefix = f"{sub}/" if sub else ""
    dirs: set[str] = set()

    rows = (
        db.query(models.BookLocation.symlink_path)
        .filter(models.BookLocation.symlink_path.like(f"{prefix}%/%"))
        .all()
    )
    for (sp,) in rows:
        rest = sp[len(prefix):]
        first = rest.split("/", 1)[0] if "/" in rest else ""
        if first and not first.startswith(".") and (sub or first not in _TOPDIR_SKIPLIST):
            dirs.add(first)

    fs_dir = os.path.join(_m().BOOKS_DIR, sub) if sub else _m().BOOKS_DIR
    if os.path.isdir(fs_dir):
        try:
            for entry in os.listdir(fs_dir):
                if entry.startswith("."):
                    continue
                if not sub and entry in _TOPDIR_SKIPLIST:
                    continue
                if os.path.isdir(os.path.join(fs_dir, entry)):
                    dirs.add(entry)
        except OSError:
            pass
    # Recommended/ is browsable but not a valid upload/move destination, so it
    # must not appear as a pickable node in the upload directory tree.
    if not sub:
        dirs.discard(RECOMMENDED_SUBDIR)
    return {"path": sub, "dirs": sorted(dirs)}


def _save_cover_for_hash(file: UploadFile, hash_id: str) -> str:
    """Resize uploaded image to 300px-wide JPEG, write to
    .data/covers/<hash>.jpg, return cache-busted URL."""
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
    _ensure_writable_dir(_m().COVERS_DIR)
    dest = os.path.join(_m().COVERS_DIR, f"{hash_id}.jpg")
    im.save(dest, format="JPEG", quality=85)
    return _cover_url_for(hash_id) or f"/api/covers/{hash_id}"


@router.put("/api/admin/books/{hash_id}/cover", response_model=schemas.CoverUpdateResponse)
async def admin_replace_book_cover(
    hash_id: str,
    file: UploadFile = File(...),
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    cover_url = _save_cover_for_hash(file, hash_id)
    _audit(db, admin, "book.cover",
           target_kind="book", target_id=hash_id,
           summary=f'Replaced cover of "{book.title or hash_id[:12]}"',
           details={"title": book.title or hash_id[:12],
                    "path": _first_book_path(db, hash_id), "mode": "upload"})
    db.commit()
    return {"cover_url": cover_url}


@router.post("/api/admin/books/{hash_id}/cover/reextract", response_model=schemas.CoverUpdateResponse)
async def admin_reextract_book_cover(
    hash_id: str,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    loc_row = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == hash_id
    ).first()
    if not loc_row:
        raise HTTPException(status_code=404, detail="Book has no registered location")
    symlink_fs = os.path.join(_m().BOOKS_DIR, loc_row[0])
    real_path = os.path.realpath(symlink_fs)
    if not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Book file missing on disk")
    fmt = _detect_format(book.original_filename or loc_row[0])
    _ensure_writable_dir(_m().COVERS_DIR)
    _ensure_writable_dir(_m().STAGING_DIR)
    dest = os.path.join(_m().COVERS_DIR, f"{hash_id}.jpg")
    # Run extraction against a copy that retains the original extension so
    # ebook-meta's dispatch works (the vault file is bare hex).
    tmp_dir = os.path.join(_m().STAGING_DIR, f".reextract-{uuid.uuid4().hex}")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        tmp_src = os.path.join(tmp_dir, book.original_filename or "book.bin")
        try:
            os.symlink(real_path, tmp_src)
        except OSError:
            shutil.copy2(real_path, tmp_src)
        result = _extract_cover_to(tmp_src, fmt, dest)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if result is None:
        raise HTTPException(status_code=500, detail="Cover extraction failed")
    cover_url = _cover_url_for(hash_id) or f"/api/covers/{hash_id}"
    _audit(db, admin, "book.cover",
           target_kind="book", target_id=hash_id,
           summary=f'Re-extracted cover of "{book.title or hash_id[:12]}"',
           details={"title": book.title or hash_id[:12],
                    "path": _first_book_path(db, hash_id), "mode": "reextract"})
    db.commit()
    return {"cover_url": cover_url}
