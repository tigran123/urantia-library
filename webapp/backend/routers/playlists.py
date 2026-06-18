"""Router module (extracted from main.py): user-owned playlists (named
collections of books AND directories), plus the dormant legacy favorites /
directory-favorites endpoints. One kind='bookshelf' playlist per user is the
non-deletable default. Playlists are a second clearance-filtered surface:
`_serialize_items` / `_dir_item_visible` / `_collage_items` reapply the same
gating as /api/browse, and the public GET /api/shared/{token} view filters per
viewer. Moved verbatim from main.py (no logic change)."""
import os
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import models
import schemas
from database import get_db
from config import _now_iso
from deps import get_current_user, get_optional_user, _clearance_of, _is_admin
from paths import (
    _safe_under_books, _accessible_locations_query, _book_clearance,
    _first_book_path,
)
from serialize import _rating_stats, _attach_recommendations
from background import _record_usage_event

router = APIRouter()


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect the
    library root via `monkeypatch.setattr(main, "BOOKS_DIR", ...)` / DATA_DIR, so
    these mutable runtime paths are read through `main` (the patch target) rather
    than bound at import. main re-exports them from config, so production reads the
    same config values. Call-time import: no load-time cycle."""
    import main
    return main


@router.get("/api/favorites")
async def get_favorites(current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if current_user is None:  # guest — no per-user state
        return {"items": []}
    q = db.query(models.Favorite, models.Book, models.BookLocation).join(
        models.Book, models.Favorite.hash_id == models.Book.id
    ).outerjoin(
        models.BookLocation, models.Book.id == models.BookLocation.hash_id
    ).filter(models.Favorite.user_id == current_user.id)
    if not current_user.is_admin:
        q = q.filter(models.Book.clearance <= (current_user.clearance or 0))
    results = q.all()

    # Pull reading progress for this user's favorites in a single query and
    # attach percent to each item — avoids N+1.
    progress_map: dict[str, float | None] = {}
    if results:
        hash_ids = {fav.hash_id for fav, _, _ in results}
        for rp in db.query(models.ReadingProgress).filter(
            models.ReadingProgress.user_id == current_user.id,
            models.ReadingProgress.hash_id.in_(hash_ids),
        ).all():
            progress_map[rp.hash_id] = rp.percent

    fav_dict = {}
    for fav, book, loc in results:
        if fav.id not in fav_dict:
            fav_dict[fav.id] = {
                "favorite_id": fav.id,
                "hash_id": fav.hash_id,
                "title": book.title,
                "author": book.author,
                "description": book.description,
                "original_filename": book.original_filename,
                "path": loc.symlink_path if loc else None,
                "percent": progress_map.get(fav.hash_id),
            }

    items = list(fav_dict.values())
    _attach_recommendations(items, db)
    return {"items": items}

@router.post("/api/favorites", response_model=schemas.FavoriteResponse)
async def add_favorite(fav: schemas.FavoriteCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin and _book_clearance(fav.hash_id, db) > (current_user.clearance or 0):
        raise HTTPException(status_code=403, detail="Forbidden")
    existing = db.query(models.Favorite).filter(
        models.Favorite.user_id == current_user.id,
        models.Favorite.hash_id == fav.hash_id
    ).first()
    if existing:
        return existing

    new_fav = models.Favorite(user_id=current_user.id, hash_id=fav.hash_id)
    db.add(new_fav)
    db.commit()
    db.refresh(new_fav)
    return new_fav

@router.delete("/api/favorites/{hash_id}")
async def remove_favorite(hash_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    fav = db.query(models.Favorite).filter(
        models.Favorite.user_id == current_user.id,
        models.Favorite.hash_id == hash_id
    ).first()
    if fav:
        db.delete(fav)
        db.commit()
    return {"message": "Removed"}


def _normalize_dir_path(path: str) -> str:
    """Validate a directory path: must be inside BOOKS_DIR, must exist, must
    actually be a directory, and (via _safe_under_books) must not be an infra
    entry in _TOPDIR_SKIPLIST or escape the tree through a symlinked component —
    so an infra path can't be parked in a playlist/favorite. Returns the
    canonical relative path (forward slashes, no trailing slash); "" is the root."""
    if path is None:
        raise HTTPException(status_code=400, detail="Invalid path")
    rel = path.strip().lstrip("/").rstrip("/")
    target = _safe_under_books(rel) if rel else os.path.abspath(_m().BOOKS_DIR)
    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="Directory not found")
    return rel


@router.get("/api/dir-favorites")
async def get_dir_favorites(
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if current_user is None:  # guest — no per-user state
        return {"items": []}
    rows = db.query(models.DirectoryFavorite).filter(
        models.DirectoryFavorite.user_id == current_user.id
    ).order_by(models.DirectoryFavorite.path).all()
    items = []
    for row in rows:
        full = os.path.abspath(os.path.join(_m().BOOKS_DIR, row.path))
        items.append({
            "id": row.id,
            "path": row.path,
            "name": os.path.basename(row.path) or row.path,
            "exists": full.startswith(os.path.abspath(_m().BOOKS_DIR)) and os.path.isdir(full),
        })
    return {"items": items}


@router.post("/api/dir-favorites", response_model=schemas.DirectoryFavoriteResponse)
async def add_dir_favorite(
    fav: schemas.DirectoryFavoriteCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rel = _normalize_dir_path(fav.path)
    existing = db.query(models.DirectoryFavorite).filter(
        models.DirectoryFavorite.user_id == current_user.id,
        models.DirectoryFavorite.path == rel,
    ).first()
    if existing:
        return existing
    new_fav = models.DirectoryFavorite(user_id=current_user.id, path=rel)
    db.add(new_fav)
    db.commit()
    db.refresh(new_fav)
    return new_fav


@router.delete("/api/dir-favorites")
async def remove_dir_favorite(
    path: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # No existence check — let users remove a stale bookmark for a deleted dir.
    rel = path.strip().lstrip("/").rstrip("/")
    row = db.query(models.DirectoryFavorite).filter(
        models.DirectoryFavorite.user_id == current_user.id,
        models.DirectoryFavorite.path == rel,
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return {"message": "Removed"}

# ============================================================================
# Playlists
#
# User-owned, named collections of books AND directories. One kind='bookshelf'
# playlist per user is the non-deletable default (supersedes My Bookshelf). A
# playlist may be shared by a stable link (/#/p/{share_token}); the public
# endpoint clearance-filters items by the *viewer's* clearance (anon = 0) and
# silently omits gated ones. Ownership is enforced on every mutating route.
# ============================================================================

def _get_or_create_bookshelf(db: Session, user: models.User) -> models.Playlist:
    """Return the user's default Bookshelf, creating it on first access. Makes
    the feature robust for users registered after migration 0006 ran.

    The INSERT may race with a concurrent first-time request from the same
    user. Migration 0008 enforces one Bookshelf per user with a partial
    unique index, so the loser raises IntegrityError — catch it and re-query.
    """
    shelf = db.query(models.Playlist).filter(
        models.Playlist.owner_id == user.id,
        models.Playlist.kind == "bookshelf",
    ).first()
    if shelf:
        return shelf
    now = _now_iso()
    shelf = models.Playlist(
        owner_id=user.id, name="Bookshelf", description=None,
        visibility="private", kind="bookshelf",
        created_at=now, updated_at=now,
    )
    db.add(shelf)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        shelf = db.query(models.Playlist).filter(
            models.Playlist.owner_id == user.id,
            models.Playlist.kind == "bookshelf",
        ).first()
        if shelf is None:
            raise
        return shelf
    db.refresh(shelf)
    return shelf


def _get_owned_playlist(db: Session, playlist_id: int, user: models.User) -> models.Playlist:
    """Fetch a playlist, 404 if missing, 403 if not owned by `user`. The 403
    wording stays generic ("Forbidden") — never names the clearance system."""
    pl = db.query(models.Playlist).filter(models.Playlist.id == playlist_id).first()
    if pl is None:
        raise HTTPException(status_code=404, detail="Not found")
    if pl.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return pl


def _dir_item_visible(db: Session, dir_path: str, user: models.User | None) -> bool:
    """A directory playlist item is visible to `user` iff they can read at least
    one book somewhere under it — same rule /api/browse uses to hide empty/gated
    subtrees. Admins bypass."""
    if _is_admin(user):
        return True
    prefix = (dir_path.rstrip("/") + "/") if dir_path else ""
    return _accessible_locations_query(db, prefix, user).first() is not None


def _serialize_items(
    db: Session,
    items: list,
    user: models.User | None,
    owner_view: bool,
    *,
    books_out: dict[str, models.Book] | None = None,
    cover_exists_out: dict[str, bool] | None = None,
) -> list[dict]:
    """Turn PlaylistItem rows into Browse-shaped card dicts (so the frontend
    reuses the same card/row components). When `owner_view` is False the result
    is clearance-filtered to `user`: gated books and directories with no
    readable book are silently dropped. Ratings + recommendations are attached
    in batch.

    Optional `books_out` / `cover_exists_out` dicts are populated as a
    side-effect so callers like `get_playlist` can reuse them for the collage
    without re-querying.
    """
    book_ids = [it.book_hash_id for it in items if it.item_type == "book" and it.book_hash_id]
    books: dict[str, models.Book] = {}
    locs: dict[str, str] = {}
    cover_exists: dict[str, bool] = {}
    if book_ids:
        for b in db.query(models.Book).filter(models.Book.id.in_(book_ids)).all():
            books[b.id] = b
        for h, p in (
            db.query(models.BookLocation.hash_id, func.min(models.BookLocation.symlink_path))
            .filter(models.BookLocation.hash_id.in_(book_ids))
            .group_by(models.BookLocation.hash_id)
            .all()
        ):
            locs[h] = p
        for bid in books:
            cover_exists[bid] = os.path.exists(os.path.join(_m().DATA_DIR, "covers", f"{bid}.jpg"))

    out: list[dict] = []
    for it in items:
        if it.item_type == "directory":
            path = it.dir_path or ""
            if not owner_view and not _dir_item_visible(db, path, user):
                continue
            full = os.path.abspath(os.path.join(_m().BOOKS_DIR, path))
            exists = full.startswith(os.path.abspath(_m().BOOKS_DIR)) and os.path.isdir(full)
            out.append({
                "id": it.id, "item_type": "directory", "is_dir": True,
                "dir_path": path, "name": os.path.basename(path) or path,
                "path": path, "exists": exists, "position": it.position,
            })
            continue
        book = books.get(it.book_hash_id)
        if book is None:
            # The book row is gone (deleted/orphaned). Hide it from recipients;
            # show the owner a "missing" placeholder so they can prune it.
            if not owner_view:
                continue
            out.append({
                "id": it.id, "item_type": "book", "is_dir": False,
                "hash_id": it.book_hash_id, "name": None, "title": None,
                "path": None, "missing": True, "position": it.position,
            })
            continue
        if not owner_view and not _is_admin(user) and (book.clearance or 0) > _clearance_of(user):
            continue
        d = {
            "id": it.id, "item_type": "book", "is_dir": False,
            "hash_id": book.id, "name": book.original_filename,
            "title": book.title, "author": book.author,
            "description": book.description, "publisher": book.publisher,
            "published": book.published, "series": book.series,
            "languages": book.languages,
            "cover_url": f"/api/covers/{book.id}" if cover_exists.get(book.id) else None,
            "path": locs.get(book.id),
            "position": it.position,
        }
        # Clearance is admin-only — never expose the value (or the existence of
        # the clearance system) to non-admins, even the playlist owner.
        if _is_admin(user):
            d["clearance"] = int(book.clearance or 0)
        out.append(d)

    _attach_recommendations(out, db)
    stats = _rating_stats(db, [it.get("hash_id") for it in out])
    for it in out:
        s = stats.get(it.get("hash_id"))
        it["avg_rating"] = s["avg_rating"] if s else None
        it["rating_count"] = s["rating_count"] if s else 0
    if books_out is not None:
        books_out.update(books)
    if cover_exists_out is not None:
        cover_exists_out.update(cover_exists)
    return out


def _collage_items(
    db: Session,
    pl: models.Playlist,
    user: models.User | None,
    owner_view: bool,
    limit: int = 4,
    *,
    prefetched_items: list | None = None,
    books_map: dict[str, models.Book] | None = None,
    cover_exists: dict[str, bool] | None = None,
) -> list[dict]:
    """First up-to-`limit` visible items as {item_type, cover_url} for the 2x2
    collage. Scans a bounded window so a long list of gated items still resolves
    cheaply.

    The three keyword-only args let callers that already have the data feed it
    in (avoids the N+1 collage pattern). `prefetched_items` is a list of
    PlaylistItem rows already in position order for this playlist (only the
    first ~40 are needed); `books_map` is hash_id → Book covering at least the
    book items in this window; `cover_exists` is hash_id → bool. When omitted,
    each is filled in via the same focused query the legacy path used.
    """
    if prefetched_items is not None:
        items = prefetched_items[:40]
    else:
        items = (
            db.query(models.PlaylistItem)
            .filter(models.PlaylistItem.playlist_id == pl.id)
            .order_by(models.PlaylistItem.position, models.PlaylistItem.id)
            .limit(40)
            .all()
        )

    # Fill any gaps in the caller-supplied maps with a single batched lookup,
    # so we never fall into per-item Book queries inside the loop below.
    if books_map is None:
        books_map = {}
    needed_book_ids = {
        it.book_hash_id for it in items
        if it.item_type == "book" and it.book_hash_id and it.book_hash_id not in books_map
    }
    if needed_book_ids:
        for b in db.query(models.Book).filter(models.Book.id.in_(needed_book_ids)).all():
            books_map[b.id] = b
    if cover_exists is None:
        cover_exists = {}
    for bid in books_map:
        if bid not in cover_exists:
            cover_exists[bid] = os.path.exists(os.path.join(_m().DATA_DIR, "covers", f"{bid}.jpg"))

    out: list[dict] = []
    for it in items:
        if len(out) >= limit:
            break
        if it.item_type == "directory":
            if not owner_view and not _dir_item_visible(db, it.dir_path or "", user):
                continue
            out.append({"item_type": "directory", "cover_url": None})
            continue
        book = books_map.get(it.book_hash_id) if it.book_hash_id else None
        if book is None:
            continue
        if not owner_view and not _is_admin(user) and (book.clearance or 0) > _clearance_of(user):
            continue
        out.append({
            "item_type": "book",
            "cover_url": f"/api/covers/{book.id}" if cover_exists.get(book.id) else None,
        })
    return out


def _playlist_card(
    db: Session,
    pl: models.Playlist,
    user: models.User,
    item_count: int,
    *,
    prefetched_items: list | None = None,
    books_map: dict[str, models.Book] | None = None,
    cover_exists: dict[str, bool] | None = None,
) -> dict:
    """Index/header summary (no full item list). Passes prefetched data through
    to `_collage_items` when supplied."""
    return {
        "id": pl.id, "name": pl.name, "description": pl.description,
        "visibility": pl.visibility, "kind": pl.kind,
        "is_bookshelf": pl.kind == "bookshelf",
        "share_token": pl.share_token,
        "item_count": item_count,
        "created_at": pl.created_at, "updated_at": pl.updated_at,
        "collage": _collage_items(
            db, pl, user, owner_view=True,
            prefetched_items=prefetched_items,
            books_map=books_map,
            cover_exists=cover_exists,
        ),
    }


def _attach_progress(db: Session, user: models.User, items: list[dict]) -> None:
    """Set `percent` on each book item from the owner's reading_progress."""
    hash_ids = {it.get("hash_id") for it in items if it.get("item_type") == "book" and it.get("hash_id")}
    if not hash_ids:
        return
    pmap = {
        rp.hash_id: rp.percent
        for rp in db.query(models.ReadingProgress).filter(
            models.ReadingProgress.user_id == user.id,
            models.ReadingProgress.hash_id.in_(hash_ids),
        ).all()
    }
    for it in items:
        if it.get("item_type") == "book":
            it["percent"] = pmap.get(it.get("hash_id"))


@router.get("/api/playlists")
async def list_playlists(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_or_create_bookshelf(db, current_user)
    pls = db.query(models.Playlist).filter(models.Playlist.owner_id == current_user.id).all()
    ids = [p.id for p in pls]
    counts: dict[int, int] = {}
    # Pre-fetch (a) per-playlist item counts and (b) the windowed item rows
    # used by every collage, so `_playlist_card` never falls into per-playlist
    # PlaylistItem/Book queries. One round-trip per concern, regardless of N.
    items_by_pl: dict[int, list] = {pid: [] for pid in ids}
    if ids:
        for pid, c in (
            db.query(models.PlaylistItem.playlist_id, func.count(models.PlaylistItem.id))
            .filter(models.PlaylistItem.playlist_id.in_(ids))
            .group_by(models.PlaylistItem.playlist_id)
            .all()
        ):
            counts[pid] = c
        # Collage looks at up to the first ~40 rows per playlist. We can't
        # SELECT LIMIT per-group cheaply in SQLite, so pull every row for the
        # owner's playlists (typical user has tens to low hundreds) ordered by
        # (playlist_id, position) and bucket in Python; the cap is then applied
        # inside `_collage_items`.
        for it in (
            db.query(models.PlaylistItem)
            .filter(models.PlaylistItem.playlist_id.in_(ids))
            .order_by(models.PlaylistItem.playlist_id, models.PlaylistItem.position, models.PlaylistItem.id)
            .all()
        ):
            bucket = items_by_pl.setdefault(it.playlist_id, [])
            if len(bucket) < 40:
                bucket.append(it)
        # One IN-query for every book referenced across every collage window,
        # plus one cover-stat pass — shared across all playlists.
        book_ids = {
            it.book_hash_id
            for bucket in items_by_pl.values()
            for it in bucket
            if it.item_type == "book" and it.book_hash_id
        }
        books_map: dict[str, models.Book] = {}
        cover_exists: dict[str, bool] = {}
        if book_ids:
            for b in db.query(models.Book).filter(models.Book.id.in_(book_ids)).all():
                books_map[b.id] = b
            for bid in book_ids:
                cover_exists[bid] = os.path.exists(os.path.join(_m().DATA_DIR, "covers", f"{bid}.jpg"))
    else:
        books_map = {}
        cover_exists = {}
    out = [
        _playlist_card(
            db, p, current_user, counts.get(p.id, 0),
            prefetched_items=items_by_pl.get(p.id, []),
            books_map=books_map,
            cover_exists=cover_exists,
        )
        for p in pls
    ]
    # Bookshelf first, then most-recently-updated. Two stable sorts.
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    out.sort(key=lambda x: not x["is_bookshelf"])
    return {"items": out}


@router.get("/api/playlists/contained-keys")
async def playlist_contained_keys(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every book hash / directory path that sits in >=1 of the caller's
    playlists — drives the filled/blue bookmark state across Browse & Search."""
    rows = (
        db.query(models.PlaylistItem.item_type, models.PlaylistItem.book_hash_id, models.PlaylistItem.dir_path)
        .join(models.Playlist, models.Playlist.id == models.PlaylistItem.playlist_id)
        .filter(models.Playlist.owner_id == current_user.id)
        .all()
    )
    book_ids = sorted({r[1] for r in rows if r[0] == "book" and r[1]})
    dir_paths = sorted({r[2] for r in rows if r[0] == "directory" and r[2] is not None})
    return {"book_hash_ids": book_ids, "dir_paths": dir_paths}


@router.get("/api/playlists/membership")
async def playlist_membership(
    book_hash_id: Optional[str] = None,
    dir_path: Optional[str] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Which of the caller's playlists already contain a given item (for the
    add-to-playlist popover checkboxes)."""
    q = (
        db.query(models.PlaylistItem.playlist_id)
        .join(models.Playlist, models.Playlist.id == models.PlaylistItem.playlist_id)
        .filter(models.Playlist.owner_id == current_user.id)
    )
    if book_hash_id:
        q = q.filter(models.PlaylistItem.item_type == "book", models.PlaylistItem.book_hash_id == book_hash_id)
    elif dir_path is not None:
        rel = dir_path.strip().lstrip("/").rstrip("/")
        q = q.filter(models.PlaylistItem.item_type == "directory", models.PlaylistItem.dir_path == rel)
    else:
        raise HTTPException(status_code=400, detail="book_hash_id or dir_path required")
    return {"playlist_ids": sorted({pid for (pid,) in q.all()})}


@router.post("/api/playlists")
async def create_playlist(body: schemas.PlaylistCreate, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    vis = body.visibility if body.visibility in ("private", "public") else "private"
    now = _now_iso()
    pl = models.Playlist(
        owner_id=current_user.id, name=name,
        description=(body.description or "").strip() or None,
        visibility=vis, kind="normal",
        share_token=secrets.token_urlsafe(12) if vis == "public" else None,
        created_at=now, updated_at=now,
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    _record_usage_event(request, "playlist_create", user=current_user,
                        path=f"playlists/{pl.id}",
                        extra={"playlist_id": pl.id, "name": pl.name, "visibility": pl.visibility})
    return _playlist_card(db, pl, current_user, 0)


@router.get("/api/playlists/{playlist_id}")
async def get_playlist(playlist_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pl = _get_owned_playlist(db, playlist_id, current_user)
    items_rows = (
        db.query(models.PlaylistItem)
        .filter(models.PlaylistItem.playlist_id == pl.id)
        .order_by(models.PlaylistItem.position, models.PlaylistItem.id)
        .all()
    )
    # Capture the books/cover maps `_serialize_items` builds and reuse them for
    # the collage — saves a duplicate PlaylistItem query plus per-cover stats.
    books_map: dict[str, models.Book] = {}
    cover_exists: dict[str, bool] = {}
    items = _serialize_items(
        db, items_rows, current_user, owner_view=True,
        books_out=books_map, cover_exists_out=cover_exists,
    )
    _attach_progress(db, current_user, items)
    card = _playlist_card(
        db, pl, current_user, len(items_rows),
        prefetched_items=items_rows,
        books_map=books_map,
        cover_exists=cover_exists,
    )
    return {"playlist": card, "items": items}


@router.patch("/api/playlists/{playlist_id}")
async def update_playlist(playlist_id: int, body: schemas.PlaylistUpdate, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pl = _get_owned_playlist(db, playlist_id, current_user)
    visibility_changed = False
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name required")
        pl.name = name
    if body.description is not None:
        pl.description = body.description.strip() or None
    if body.visibility is not None:
        if body.visibility not in ("private", "public"):
            raise HTTPException(status_code=400, detail="Invalid visibility")
        visibility_changed = body.visibility != pl.visibility
        pl.visibility = body.visibility
        if body.visibility == "public" and not pl.share_token:
            pl.share_token = secrets.token_urlsafe(12)
        # Going private keeps the token (stable links) — re-sharing reuses it.
    pl.updated_at = _now_iso()
    db.commit()
    db.refresh(pl)
    if visibility_changed:
        _record_usage_event(request, "playlist_visibility", user=current_user,
                            path=f"playlists/{pl.id}",
                            extra={"playlist_id": pl.id, "name": pl.name, "visibility": pl.visibility})
    count = db.query(func.count(models.PlaylistItem.id)).filter(models.PlaylistItem.playlist_id == pl.id).scalar() or 0
    return _playlist_card(db, pl, current_user, count)


@router.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pl = _get_owned_playlist(db, playlist_id, current_user)
    if pl.kind == "bookshelf":
        raise HTTPException(status_code=403, detail="Forbidden")
    pl_name = pl.name
    # FK enforcement (database.py) cascades playlist_items on playlist delete.
    db.delete(pl)
    db.commit()
    _record_usage_event(request, "playlist_delete", user=current_user,
                        path=f"playlists/{playlist_id}",
                        extra={"playlist_id": playlist_id, "name": pl_name})
    return {"message": "Deleted"}


@router.post("/api/playlists/{playlist_id}/items")
async def add_playlist_item(playlist_id: int, body: schemas.PlaylistItemAdd, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pl = _get_owned_playlist(db, playlist_id, current_user)
    has_book = bool(body.book_hash_id)
    has_dir = body.dir_path is not None
    if has_book == has_dir:
        raise HTTPException(status_code=400, detail="Provide exactly one of book_hash_id or dir_path")

    def _find_existing() -> models.PlaylistItem | None:
        q = db.query(models.PlaylistItem).filter(models.PlaylistItem.playlist_id == pl.id)
        if has_book:
            return q.filter(
                models.PlaylistItem.item_type == "book",
                models.PlaylistItem.book_hash_id == body.book_hash_id,
            ).first()
        return q.filter(
            models.PlaylistItem.item_type == "directory",
            models.PlaylistItem.dir_path == rel,
        ).first()

    if has_book:
        # Owner must be able to see the book they're adding (mirrors add_favorite).
        if not _is_admin(current_user) and _book_clearance(body.book_hash_id, db) > _clearance_of(current_user):
            raise HTTPException(status_code=403, detail="Forbidden")
        rel = None
    else:
        rel = _normalize_dir_path(body.dir_path)

    existing = _find_existing()
    if existing:
        return {"id": existing.id, "item_type": existing.item_type,
                "book_hash_id": existing.book_hash_id, "dir_path": existing.dir_path}

    if has_book:
        new_item = models.PlaylistItem(
            playlist_id=pl.id, item_type="book", book_hash_id=body.book_hash_id,
            position=_next_position(db, pl.id), added_at=_now_iso(),
        )
    else:
        new_item = models.PlaylistItem(
            playlist_id=pl.id, item_type="directory", dir_path=rel,
            position=_next_position(db, pl.id), added_at=_now_iso(),
        )

    db.add(new_item)
    pl.updated_at = _now_iso()
    try:
        db.commit()
    except IntegrityError:
        # A concurrent add (double-click / popover double-fire) won the race and
        # already inserted the same book/dir — the partial unique index rejected
        # ours. Treat as idempotent: return the row that's now there.
        db.rollback()
        existing = _find_existing()
        if existing is None:
            raise
        return {"id": existing.id, "item_type": existing.item_type,
                "book_hash_id": existing.book_hash_id, "dir_path": existing.dir_path}
    db.refresh(new_item)
    if new_item.item_type == "book":
        _record_usage_event(request, "playlist_add_item", user=current_user,
                            hash_id=new_item.book_hash_id,
                            path=_first_book_path(db, new_item.book_hash_id),
                            extra={"playlist_id": pl.id, "item_type": "book"})
    else:
        _record_usage_event(request, "playlist_add_item", user=current_user,
                            path=new_item.dir_path,
                            extra={"playlist_id": pl.id, "item_type": "directory"})
    return {"id": new_item.id, "item_type": new_item.item_type,
            "book_hash_id": new_item.book_hash_id, "dir_path": new_item.dir_path}


def _next_position(db: Session, playlist_id: int) -> int:
    mx = db.query(func.max(models.PlaylistItem.position)).filter(
        models.PlaylistItem.playlist_id == playlist_id
    ).scalar()
    return (mx + 1) if mx is not None else 0


@router.delete("/api/playlists/{playlist_id}/items/{item_id}")
async def remove_playlist_item(playlist_id: int, item_id: int, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pl = _get_owned_playlist(db, playlist_id, current_user)
    item = db.query(models.PlaylistItem).filter(
        models.PlaylistItem.id == item_id,
        models.PlaylistItem.playlist_id == pl.id,
    ).first()
    if item:
        item_type, book_hash_id, dir_path = item.item_type, item.book_hash_id, item.dir_path
        db.delete(item)
        pl.updated_at = _now_iso()
        db.commit()
        _record_usage_event(request, "playlist_remove_item", user=current_user,
                            hash_id=book_hash_id if item_type == "book" else None,
                            path=_first_book_path(db, book_hash_id) if item_type == "book" else dir_path,
                            extra={"playlist_id": pl.id, "item_type": item_type})
    return {"message": "Removed"}


@router.delete("/api/playlists/{playlist_id}/items")
async def remove_playlist_item_by_target(
    playlist_id: int,
    request: Request,
    book_hash_id: Optional[str] = None,
    dir_path: Optional[str] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a book/directory from a playlist by its identity rather than its
    row id — what the add-to-playlist popover uses to toggle membership."""
    pl = _get_owned_playlist(db, playlist_id, current_user)
    q = db.query(models.PlaylistItem).filter(models.PlaylistItem.playlist_id == pl.id)
    if book_hash_id:
        q = q.filter(models.PlaylistItem.item_type == "book", models.PlaylistItem.book_hash_id == book_hash_id)
    elif dir_path is not None:
        rel = dir_path.strip().lstrip("/").rstrip("/")
        q = q.filter(models.PlaylistItem.item_type == "directory", models.PlaylistItem.dir_path == rel)
    else:
        raise HTTPException(status_code=400, detail="book_hash_id or dir_path required")
    item = q.first()
    if item:
        item_type, b_hash, d_path = item.item_type, item.book_hash_id, item.dir_path
        db.delete(item)
        pl.updated_at = _now_iso()
        db.commit()
        _record_usage_event(request, "playlist_remove_item", user=current_user,
                            hash_id=b_hash if item_type == "book" else None,
                            path=_first_book_path(db, b_hash) if item_type == "book" else d_path,
                            extra={"playlist_id": pl.id, "item_type": item_type})
    return {"message": "Removed"}


@router.put("/api/playlists/{playlist_id}/order")
async def reorder_playlist(playlist_id: int, body: schemas.PlaylistOrderUpdate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    # OWNER ONLY — a recipient of a shared list must never reorder it. This is
    # the server-side enforcement; the UI also hides the drag handle.
    pl = _get_owned_playlist(db, playlist_id, current_user)
    items = db.query(models.PlaylistItem).filter(models.PlaylistItem.playlist_id == pl.id).all()
    by_id = {it.id: it for it in items}
    if set(body.item_ids) != set(by_id.keys()) or len(body.item_ids) != len(by_id):
        raise HTTPException(status_code=400, detail="item_ids must be a permutation of the playlist's items")
    for pos, item_id in enumerate(body.item_ids):
        by_id[item_id].position = pos
    pl.updated_at = _now_iso()
    db.commit()
    return {"message": "Reordered"}


@router.post("/api/playlists/{playlist_id}/share")
async def share_playlist(playlist_id: int, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pl = _get_owned_playlist(db, playlist_id, current_user)
    was_public = pl.visibility == "public"
    pl.visibility = "public"
    if not pl.share_token:
        pl.share_token = secrets.token_urlsafe(12)
    pl.updated_at = _now_iso()
    db.commit()
    db.refresh(pl)
    if not was_public:
        _record_usage_event(request, "playlist_share", user=current_user,
                            path=f"playlists/{pl.id}",
                            extra={"playlist_id": pl.id, "name": pl.name})
    return {"token": pl.share_token, "visibility": pl.visibility}


@router.delete("/api/playlists/{playlist_id}/share")
async def unshare_playlist(playlist_id: int, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pl = _get_owned_playlist(db, playlist_id, current_user)
    was_public = pl.visibility == "public"
    pl.visibility = "private"
    # Keep share_token: going private just disables /api/shared/{token} (404);
    # re-sharing reactivates the same link.
    pl.updated_at = _now_iso()
    db.commit()
    if was_public:
        _record_usage_event(request, "playlist_visibility", user=current_user,
                            path=f"playlists/{playlist_id}",
                            extra={"playlist_id": playlist_id, "name": pl.name, "visibility": "private"})
    return {"visibility": "private"}


@router.post("/api/playlists/{playlist_id}/share-link-copied")
async def note_share_link_copied(playlist_id: int, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Telemetry-only: the owner clicked "Copy link" in the Share dialog. Records
    a playlist_link_copy usage event and does nothing else (no state change).
    Owner-gated via _get_owned_playlist so the count reflects deliberate
    link-grabs by the playlist owner; the frontend calls it fire-and-forget."""
    pl = _get_owned_playlist(db, playlist_id, current_user)
    _record_usage_event(request, "playlist_link_copy", user=current_user,
                        path=f"playlists/{pl.id}",
                        extra={"playlist_id": pl.id, "name": pl.name})
    return {"ok": True}


@router.get("/api/shared/{token}")
async def get_shared_playlist(token: str, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    """PUBLIC. Returns a public playlist's clearance-filtered contents for any
    viewer (anon = clearance 0). Gated items are silently omitted. 404 when the
    token is unknown or the playlist is currently private."""
    pl = db.query(models.Playlist).filter(models.Playlist.share_token == token).first()
    if pl is None or pl.visibility != "public":
        raise HTTPException(status_code=404, detail="Not found")
    items_rows = (
        db.query(models.PlaylistItem)
        .filter(models.PlaylistItem.playlist_id == pl.id)
        .order_by(models.PlaylistItem.position, models.PlaylistItem.id)
        .all()
    )
    books_map: dict[str, models.Book] = {}
    cover_exists: dict[str, bool] = {}
    items = _serialize_items(
        db, items_rows, current_user, owner_view=False,
        books_out=books_map, cover_exists_out=cover_exists,
    )
    owner = db.query(models.User).filter(models.User.id == pl.owner_id).first()
    # is_owner lets the viewer flip back to the owner-side detail page without
    # relying on browser-history (and survives an "open in new tab" preview).
    is_owner = bool(current_user is not None and current_user.id == pl.owner_id)
    return {
        "playlist": {
            "id": pl.id, "name": pl.name, "description": pl.description,
            "visibility": pl.visibility, "kind": pl.kind,
            "updated_at": pl.updated_at, "created_at": pl.created_at,
            "item_count": len(items),
            "owner_name": owner.real_name if owner else None,  # never email (PII)
            "is_owner": is_owner,
            "collage": _collage_items(
                db, pl, current_user, owner_view=False,
                prefetched_items=items_rows,
                books_map=books_map,
                cover_exists=cover_exists,
            ),
        },
        "items": items,
    }


@router.post("/api/shared/{token}/copy")
async def copy_shared_playlist(token: str, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Signed-in viewer makes an owned, private copy snapshotting only the items
    they were allowed to see. The new playlist is theirs to edit/reorder."""
    pl = db.query(models.Playlist).filter(models.Playlist.share_token == token).first()
    if pl is None or pl.visibility != "public":
        raise HTTPException(status_code=404, detail="Not found")
    src_items = (
        db.query(models.PlaylistItem)
        .filter(models.PlaylistItem.playlist_id == pl.id)
        .order_by(models.PlaylistItem.position, models.PlaylistItem.id)
        .all()
    )
    now = _now_iso()
    copy = models.Playlist(
        owner_id=current_user.id, name=pl.name, description=pl.description,
        visibility="private", kind="normal", created_at=now, updated_at=now,
    )
    db.add(copy)
    db.flush()  # assign copy.id
    pos = 0
    for it in src_items:
        # Only copy what the caller may see (clearance-filtered, like the view).
        if it.item_type == "directory":
            if not _dir_item_visible(db, it.dir_path or "", current_user):
                continue
            db.add(models.PlaylistItem(
                playlist_id=copy.id, item_type="directory", dir_path=it.dir_path,
                position=pos, added_at=now,
            ))
            pos += 1
        else:
            book = db.query(models.Book).filter(models.Book.id == it.book_hash_id).first()
            if book is None:
                continue
            if not _is_admin(current_user) and (book.clearance or 0) > _clearance_of(current_user):
                continue
            db.add(models.PlaylistItem(
                playlist_id=copy.id, item_type="book", book_hash_id=it.book_hash_id,
                position=pos, added_at=now,
            ))
            pos += 1
    db.commit()
    db.refresh(copy)
    _record_usage_event(request, "playlist_copy", user=current_user,
                        path=f"shared/{token}",
                        extra={
                            "source_playlist_id": pl.id,
                            "source_owner_id": pl.owner_id,
                            "new_playlist_id": copy.id,
                            "name": pl.name,
                            "item_count": pos,
                            "self_copy": current_user.id == pl.owner_id,
                        })
    return {"id": copy.id, "item_count": pos}
