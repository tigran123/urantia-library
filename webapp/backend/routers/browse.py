"""Router module (extracted from main.py): the directory-browsing surface —
/api/library-stats lives in auth.py, but /api/browse, /api/album-subtree, and
/api/item all route through `_list_directory`, the single clearance code path
that applies the BOOKS_DIR traversal guard, the non-admin subdirectory
clearance filter, the per-book gate, and ratings/locations/recommendations
enrichment. Moved verbatim from main.py (no logic change).

Mutable runtime paths the tests monkeypatch on `main` (BOOKS_DIR) are read via
the call-time `_m()` shim so the patch target stays effective."""
import os
import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_, func

import models
from database import get_db
from config import _AUDIO_EXTS, _TOPDIR_SKIPLIST, _escape_like
from deps import get_optional_user, _clearance_of, _is_admin
from paths import (
    _safe_under_books, _accessible_locations_query, _subtree_is_unmanaged,
    _resolves_into_infra,
)
from cas import _resolve_vault_hash
from serialize import _rating_stats, _attach_recommendations
from background import _record_usage_event

router = APIRouter()


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect the
    library root via `monkeypatch.setattr(main, "BOOKS_DIR", ...)`, so the
    mutable runtime path is read through `main` (the patch target) rather than
    bound at import. main re-exports it from config, so production reads the
    same config value. Call-time import: no load-time cycle."""
    import main
    return main


def _list_directory(path: str, current_user: models.User | None, db: Session):
    """List one directory with the exact filtering/enrichment /api/browse applies:
    the BOOKS_DIR traversal guard, the non-admin subdirectory clearance filter, the
    per-book clearance gate, and ratings/locations/recommendations enrichment.

    Returns (items, accessible_rows): `accessible_rows` is the
    _accessible_locations_query result for non-admins (None for admins) — browse
    reuses it for the subtree_has_audio scan without a second query. Raises
    HTTPException(403/404) exactly like browse. Shared by /api/browse and the
    /api/album-subtree walk so there is one clearance code path."""
    BOOKS_DIR = _m().BOOKS_DIR
    # path="" is the library root; any non-empty path goes through the full
    # traversal guard — realpath() containment AND _TOPDIR_SKIPLIST rejection —
    # so a signed-in user can't navigate into infra dirs (.data, urantia-library,
    # .claude, …) or escape the tree via a symlinked directory component. The
    # weak lexical startswith() check alone allowed both; the foreign-file reveal
    # below removed the subtree-gate 403 that used to mask this for non-admins,
    # so the strong guard is now load-bearing. The entry loop still
    # skiplist-filters the root's own children.
    if path:
        target_dir = _safe_under_books(path)
    else:
        target_dir = os.path.abspath(BOOKS_DIR)
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    # For non-admins, hide subdirectories whose subtree contains no readable book
    # (and 403 on direct access to such a directory) so the topic structure of
    # the library isn't leaked via directory names. A signed-in user is also let
    # in if the subtree is an un-imported area (files but no registered book —
    # /Books/Unsorted), whose contents are foreign files readable by any
    # logged-in user.
    accessible_subdirs: set[str] = set()
    rows = None
    if not _is_admin(current_user):
        prefix = f"{path.rstrip('/')}/" if path else ""
        rows = _accessible_locations_query(db, prefix, current_user).all()
        if path and not rows:
            # Guests are denied outright; a subtree that holds any registered book
            # (gated for this user) is a managed topic and stays 403 for everyone
            # (no-leak invariant) — only a genuinely un-imported area opens up.
            # _subtree_is_unmanaged only runs here, in the already-cold "no
            # readable book" branch — never on /api/item's hot path.
            if current_user is None or not _subtree_is_unmanaged(target_dir, db):
                raise HTTPException(status_code=403, detail="Forbidden")
        for (sp,) in rows:
            rest = sp[len(prefix):]
            if "/" in rest:
                accessible_subdirs.add(rest.split("/", 1)[0])

    items = []

    try:
        entries = sorted(os.listdir(target_dir))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    for entry in entries:
        if entry in _TOPDIR_SKIPLIST:
            continue

        entry_path = os.path.join(target_dir, entry)
        if not os.path.exists(entry_path):
            continue
        # A symlink whose realpath resolves into infra (.data subdirs, the repo)
        # is never surfaced — to anyone, admins included — even though its lexical
        # path is benign. Mirrors the realpath-target guard in _safe_under_books
        # so the listing and the content endpoints agree on what's reachable;
        # flat .data/<hash> vault files (real books) are exempt and list normally.
        if os.path.islink(entry_path) and _resolves_into_infra(entry_path):
            continue
        is_dir = os.path.isdir(entry_path)
        if is_dir and not _is_admin(current_user) and entry not in accessible_subdirs:
            # Hidden because no readable registered book lives under it. Reveal it
            # to signed-in users only when it's an un-imported area (files but no
            # registered book); keep it hidden from guests and from any subtree
            # that holds a gated registered book (no-leak invariant).
            if current_user is None or not _subtree_is_unmanaged(entry_path, db):
                continue

        # Resolve a file entry's vault hash + book row once. A non-dir entry with
        # no books row is "foreign" (a plain file, a non-vault symlink, or an
        # orphan vault symlink): visible to any signed-in user but never to
        # guests. Registered books keep their per-book clearance gate.
        file_hash = _resolve_vault_hash(entry_path) if (not is_dir and os.path.islink(entry_path)) else None
        book = db.query(models.Book).filter(models.Book.id == file_hash).first() if file_hash else None
        if not is_dir and book is None:
            if current_user is None:
                continue  # guests never see foreign files
        elif book is not None and not _is_admin(current_user) and (book.clearance or 0) > _clearance_of(current_user):
            continue  # gated registered book this user can't read

        try:
            size = os.path.getsize(entry_path) if not is_dir else 0
            mtime = datetime.fromtimestamp(os.path.getmtime(entry_path)).isoformat()
        except OSError:
            size = 0
            mtime = None

        item_data = {
            "name": entry,
            "is_dir": is_dir,
            "description": "",
            "cover_url": None,
            "size": size,
            "mtime": mtime,
            "path": os.path.relpath(entry_path, BOOKS_DIR).replace("\\", "/")
        }

        # hash_id/cover/metadata only for registered books; a foreign file (book
        # is None) stays hash-less so the frontend treats it as a plain file and
        # skips per-hash progress/rating/annotation calls (which would 404).
        if book is not None:
            item_data["hash_id"] = file_hash
            cover_fs_path = os.path.join(BOOKS_DIR, ".data", "covers", f"{file_hash}.jpg")
            if os.path.exists(cover_fs_path):
                item_data["cover_url"] = f"/api/covers/{file_hash}"
            if book.title:
                item_data["title"] = book.title
            if book.author:
                item_data["author"] = book.author
            if book.description:
                item_data["description"] = book.description
            if book.publisher:
                item_data["publisher"] = book.publisher
            if book.published:
                item_data["published"] = book.published
            if book.tags:
                item_data["tags"] = book.tags
            if book.series:
                item_data["series"] = book.series
            if book.languages:
                item_data["languages"] = book.languages
            if book.identifiers:
                item_data["identifiers"] = book.identifiers
            item_data["clearance"] = int(book.clearance or 0)
            item_data["import_date"] = book.import_date
            # Audio/video media facts (NULL for other formats); the Album
            # view reads these instead of probing each file client-side.
            if book.duration is not None:
                item_data["duration"] = book.duration
            if book.bitrate is not None:
                item_data["bitrate"] = book.bitrate
            if _is_admin(current_user):
                item_data["last_verified_at"] = book.last_verified_at
                item_data["last_verified_ok"] = book.last_verified_ok
                item_data["last_verified_mode"] = book.last_verified_mode
                item_data["last_verified_error"] = book.last_verified_error

        items.append(item_data)

    # Sort: folders first, then files
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    # Attach average ratings in one GROUP BY over the already-filtered items.
    stats = _rating_stats(db, [it.get("hash_id") for it in items])
    for it in items:
        s = stats.get(it.get("hash_id"))
        it["avg_rating"] = s["avg_rating"] if s else None
        it["rating_count"] = s["rating_count"] if s else 0

    # Attach every symlink_path under which each managed book is reachable. A
    # single book can be hard-linked into more than one topic; ItemView shows
    # the full list so users see all the places they can find it.
    #
    # Per CLAUDE.md, any endpoint that lists directories/paths must route
    # through the same clearance check as `_accessible_locations_query`.
    # Each `hash_id` here belongs to an item that already passed the per-book
    # clearance gate at L1063-66, so the JOIN below is redundant in steady
    # state — but it's defense-in-depth against any future regression that
    # lets a high-clearance book leak into the items list.
    hash_ids = [it.get("hash_id") for it in items if it.get("hash_id")]
    if hash_ids:
        loc_q = db.query(models.BookLocation.hash_id, models.BookLocation.symlink_path).filter(
            models.BookLocation.hash_id.in_(hash_ids)
        )
        if not _is_admin(current_user):
            loc_q = loc_q.join(
                models.Book, models.Book.id == models.BookLocation.hash_id
            ).filter(models.Book.clearance <= _clearance_of(current_user))
        loc_rows = loc_q.all()
        locs_by_hash: dict[str, list[str]] = {}
        for h, p in loc_rows:
            locs_by_hash.setdefault(h, []).append(p)
        for it in items:
            h = it.get("hash_id")
            if h:
                it["locations"] = sorted(locs_by_hash.get(h, []))

    _attach_recommendations(items, db)
    return items, rows


@router.get("/api/browse")
async def browse(request: Request, path: str = "", current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    items, rows = _list_directory(path, current_user, db)

    # Whether any audio lives in this directory's subtree — drives the Album view
    # toggle for directories (e.g. an artist directory of album subdirectories) that
    # contain no direct audio files of their own. Resolved from the listing when
    # possible; otherwise one indexed prefix scan over book_locations,
    # clearance-filtered for non-admins (reusing the materialized `rows`) so the flag
    # can't leak gated audio. The scan only sees imported audio — direct un-imported
    # files are covered by the listing check. Known, deliberate gap: audio that is
    # *only* in un-imported nested subdirectories won't flip this flag (a per-browse
    # os.walk would pay the whole-subtree cost on every non-audio directory just to
    # return False); it resolves once that subtree is imported. This now also covers
    # all-foreign audio dirs a logged-in user can newly see (e.g. un-imported .mp3s
    # in /Books/Unsorted): the dir lists, but the Album toggle stays off until those
    # files are imported — the safe under-offering direction, never a gated-audio leak.
    has_direct_audio = any(
        not it["is_dir"] and "." in it["name"] and it["name"].rsplit(".", 1)[1].lower() in _AUDIO_EXTS
        for it in items
    )
    if has_direct_audio:
        subtree_has_audio = True
    elif not any(it["is_dir"] for it in items):
        subtree_has_audio = False
    elif rows is not None:  # non-admin: reuse the accessible locations materialized in _list_directory
        subtree_has_audio = any(
            "." in sp and sp.rsplit(".", 1)[1].lower() in _AUDIO_EXTS for (sp,) in rows
        )
    else:
        prefix = f"{path.rstrip('/')}/" if path else ""
        audio_q = db.query(models.BookLocation.symlink_path).filter(
            models.BookLocation.symlink_path.like(f"{_escape_like(prefix)}%", escape="\\"),
            or_(*(func.lower(models.BookLocation.symlink_path).like(f"%.{e}") for e in _AUDIO_EXTS)),
        )
        subtree_has_audio = audio_q.first() is not None

    _record_usage_event(request, "page", user=current_user, path=path or "/")
    return {"path": path, "items": items, "subtree_has_audio": subtree_has_audio}


# Bounds for the recursive Album walk (mirror the caps the old client walker used).
_ALBUM_MAX_DIRS = 200
_ALBUM_MAX_TRACKS = 1000


@router.get("/api/album-subtree")
async def album_subtree(request: Request, path: str = "", current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    """Bounded, clearance-filtered recursive walk of `path`, returning audio tracks
    grouped by the subdirectory they live in (tree order). Replaces the old
    client-side walk that fetched /api/browse?probe=1 per subdirectory: one request,
    server-bounded. This is a data fetch for a directory the user already navigated
    to — /api/browse logged that `page` event — so it records none of its own, or the
    directory's page views would be double-counted. Reuses _list_directory for
    identical gating (imported + un-imported audio, per-book clearance, hidden subdirs)."""
    groups: list[dict] = []
    state = {"dirs": 0, "tracks": 0}

    def collect(rel_path: str, name: str, is_root: bool = False):
        if state["dirs"] >= _ALBUM_MAX_DIRS or state["tracks"] >= _ALBUM_MAX_TRACKS:
            return
        state["dirs"] += 1
        try:
            items, _rows = _list_directory(rel_path, current_user, db)
        except HTTPException:
            if is_root:
                raise          # invalid/forbidden root → real 403/404, like browse
            return             # gated or vanished subdir → skip silently
        audio = [
            it for it in items
            if not it["is_dir"] and "." in it["name"]
            and it["name"].rsplit(".", 1)[1].lower() in _AUDIO_EXTS
        ]
        if audio:
            state["tracks"] += len(audio)
            groups.append({"path": rel_path, "name": name, "tracks": audio})
        for it in items:
            if not it["is_dir"]:
                continue
            if state["dirs"] >= _ALBUM_MAX_DIRS or state["tracks"] >= _ALBUM_MAX_TRACKS:
                break
            collect(it["path"], it["name"])

    # The walk is synchronous (os.listdir + per-directory DB queries, up to
    # _ALBUM_MAX_DIRS deep), so run it off the event loop — otherwise a deep
    # subtree would block every other request for the duration of the walk
    # (browse pays the per-directory cost once; this walk pays it up to 200x).
    # Reusing the request-scoped Session in a worker thread is safe here: the
    # engine sets check_same_thread=False and this coroutine just awaits the
    # walk, so nothing else touches `db` meanwhile. A root HTTPException raised
    # inside the thread propagates out of to_thread unchanged (real 403/404).
    def _walk():
        root_name = os.path.basename(path.rstrip("/")) or "Library"
        collect(path, root_name, is_root=True)

    await asyncio.to_thread(_walk)
    return {"path": path, "groups": groups}


@router.get("/api/item")
async def get_item(path: str = "", current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    """One file's enriched metadata — the same item shape /api/browse returns for
    a single listing entry — looked up by listing its parent directory and
    matching the filename. The reader/ItemView needs the full dict (title, cover,
    ratings, locations, media facts) for a deep-linked or reloaded file.

    Unlike /api/browse this records NO `page` usage event: opening a book is not a
    directory navigation, and routing this lookup through /api/browse logged a
    phantom page view for the parent directory on every book open (double-count).
    Reuses _list_directory for identical clearance gating + enrichment, so a file
    the caller can't read (gated, or absent) is simply not in the listing → 404."""
    p = (path or "").strip("/")
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    parent, _, name = p.rpartition("/")
    items, _rows = _list_directory(parent, current_user, db)
    found = next((it for it in items if it["name"] == name), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Not found")
    return found
