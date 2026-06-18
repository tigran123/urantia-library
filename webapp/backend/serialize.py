"""Foundation module (extracted from main.py): small serialisation/aggregation
helpers — rating stats, recommendation attachment, and the public author
display name. Depends on models/database/config + sqlalchemy."""
import os
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session
import models


def _m():
    """Lazy handle to the fully-imported `main` module. `_cover_url_for` reads the
    vault path through `main` (the test monkeypatch target) — main re-exports
    DATA_DIR from config, so production reads the same value. Call-time import:
    no load-time serialize<->main cycle."""
    import main
    return main


def _cover_url_for(hash_id: str) -> Optional[str]:
    """Return a cache-busted URL for the book's cover if the JPEG exists in
    the vault, else None."""
    cover_path = os.path.join(_m().DATA_DIR, "covers", f"{hash_id}.jpg")
    try:
        mtime = int(os.path.getmtime(cover_path))
    except OSError:
        return None
    return f"/api/covers/{hash_id}?v={mtime}"


def _book_to_admin_detail(book: models.Book, db: Session) -> dict:
    locs = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == book.id
    ).all()
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "publisher": book.publisher,
        "published": book.published,
        "description": book.description,
        "tags": book.tags,
        "series": book.series,
        "languages": book.languages,
        "identifiers": book.identifiers,
        "original_filename": book.original_filename,
        "clearance": int(book.clearance or 0),
        "needs_review": bool(book.needs_review),
        "locations": [r[0] for r in locs],
        "cover_url": _cover_url_for(book.id),
        "last_verified_at": book.last_verified_at,
        "last_verified_ok": book.last_verified_ok,
        "last_verified_mode": book.last_verified_mode,
        "last_verified_error": book.last_verified_error,
    }


def _rating_stats(db: Session, hash_ids) -> dict[str, dict]:
    """avg_rating + rating_count per hash_id, computed in ONE GROUP BY query.
    Hashes with no ratings are simply absent from the returned dict — callers
    default those to {avg_rating: None, rating_count: 0}."""
    ids = {h for h in hash_ids if h}
    if not ids:
        return {}
    rows = (
        db.query(
            models.BookRating.hash_id,
            func.avg(models.BookRating.rating),
            func.count(models.BookRating.id),
        )
        .filter(models.BookRating.hash_id.in_(ids))
        .group_by(models.BookRating.hash_id)
        .all()
    )
    return {
        hid: {"avg_rating": round(float(avg), 3), "rating_count": int(cnt)}
        for hid, avg, cnt in rows
    }

def _attach_recommendations(items: list[dict], db: Session) -> None:
    """For each item dict carrying a `hash_id`, set `is_recommended` (bool)
    and — when recommended — `recommended_at` + `recommended_by_name`. One
    LEFT JOIN against book_recommendations + users for the full batch. Items
    without a hash_id (directories, unmanaged files) are left untouched.

    `recommended_by_name` is the admin's real_name only — never their email.
    Email is PII and these results flow out to anonymous browsers via
    /api/browse and /api/search; an admin who hasn't filled in a real_name
    shows up as None (frontend falls back to a generic "Recommended" label).
    The User join is a LEFT JOIN so a hard-deleted admin row leaves the
    recommendation visible rather than silently flipping it off."""
    hash_ids = [it["hash_id"] for it in items if it.get("hash_id")]
    if not hash_ids:
        return
    rows = (
        db.query(
            models.BookRecommendation.hash_id,
            models.BookRecommendation.recommended_at,
            models.User.real_name,
        )
        .outerjoin(models.User, models.User.id == models.BookRecommendation.recommended_by)
        .filter(models.BookRecommendation.hash_id.in_(hash_ids))
        .all()
    )
    recs = {h: (at, rn) for h, at, rn in rows}
    for it in items:
        h = it.get("hash_id")
        if not h:
            continue
        if h in recs:
            it["is_recommended"] = True
            it["recommended_at"] = recs[h][0]
            it["recommended_by_name"] = recs[h][1]
        else:
            it["is_recommended"] = False

def _author_name(email: str, real_name: str | None = None) -> str:
    """Public display name for a commenter — their chosen real name if they set
    one, otherwise the local-part of their email (so we never expose the full
    address to other users)."""
    if real_name and real_name.strip():
        return real_name.strip()
    return (email or "user").split("@", 1)[0]
