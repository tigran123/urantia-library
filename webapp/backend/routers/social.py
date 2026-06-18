"""Router module (extracted from main.py): the social layer over books —
1-5 star ratings (unmoderated), comments (moderated, one level of replies), and
annotations (per-format anchors; public ones moderated). Includes the admin
moderation queues and the throttled comment-moderation digest. Moved verbatim
from main.py (no logic change)."""
import json
import threading
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func

import models
import schemas
import email_utils
from database import get_db
from config import _now_iso
from deps import get_current_user, get_optional_user, require_admin, _clearance_of, _is_admin
from paths import _book_clearance
from serialize import _author_name
from background import _audit

router = APIRouter()


# ==============================================================================
# Ratings
# ==============================================================================
# A 1-5 star rating, one per user per book. Not moderated — it counts toward the
# book's average as soon as it is submitted.

@router.get("/api/books/{hash_id}/rating", response_model=schemas.RatingResponse)
async def get_my_rating(hash_id: str, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if current_user is None:  # guest — no personal rating
        return {"hash_id": hash_id, "rating": None}
    if not _is_admin(current_user) and _book_clearance(hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    row = db.query(models.BookRating).filter(
        models.BookRating.user_id == current_user.id,
        models.BookRating.hash_id == hash_id,
    ).first()
    return {"hash_id": hash_id, "rating": row.rating if row else None}


@router.post("/api/books/{hash_id}/rating", response_model=schemas.RatingResponse)
async def set_my_rating(hash_id: str, payload: schemas.RatingCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    if not current_user.is_admin and _book_clearance(hash_id, db) > (current_user.clearance or 0):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not db.query(models.Book.id).filter(models.Book.id == hash_id).first():
        raise HTTPException(status_code=404, detail="Book not found")
    now = _now_iso()
    row = db.query(models.BookRating).filter(
        models.BookRating.user_id == current_user.id,
        models.BookRating.hash_id == hash_id,
    ).first()
    if row:
        row.rating = payload.rating
        row.updated_at = now
    else:
        row = models.BookRating(
            user_id=current_user.id, hash_id=hash_id, rating=payload.rating,
            created_at=now, updated_at=now,
        )
        db.add(row)
    db.commit()
    return {"hash_id": hash_id, "rating": payload.rating}


@router.delete("/api/books/{hash_id}/rating")
async def delete_my_rating(hash_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(models.BookRating).filter(
        models.BookRating.user_id == current_user.id,
        models.BookRating.hash_id == hash_id,
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return {"message": "Removed"}


# ==============================================================================
# Comments
# ==============================================================================
# Text comments are moderated: they are created 'pending' and become public only
# after an admin approves them. A comment may have one level of replies.

_COMMENT_MAX_LEN = 5000
MODERATION_DIGEST_INTERVAL_HOURS = 6
_MODERATION_META_KEY = "moderation_email_last_sent_at"




def _maybe_send_moderation_digest(db: Session) -> None:
    """Best-effort: if pending comments exist and >6h elapsed since the last
    digest, email the admin a summary. Race-free — the conditional UPDATE on
    app_meta succeeds for exactly one of any concurrent callers."""
    try:
        pending = db.query(func.count(models.BookComment.id)).filter(
            models.BookComment.status == "pending"
        ).scalar() or 0
        if pending <= 0:
            return
        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        cutoff_iso = (now - timedelta(hours=MODERATION_DIGEST_INTERVAL_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Seed the throttle row once so the conditional UPDATE has something to match.
        if not db.query(models.AppMeta).filter(models.AppMeta.key == _MODERATION_META_KEY).first():
            try:
                db.add(models.AppMeta(key=_MODERATION_META_KEY, value="1970-01-01T00:00:00Z"))
                db.commit()
            except Exception:
                db.rollback()
        updated = db.query(models.AppMeta).filter(
            models.AppMeta.key == _MODERATION_META_KEY,
            models.AppMeta.value < cutoff_iso,
        ).update({models.AppMeta.value: now_iso}, synchronize_session=False)
        db.commit()
        if updated == 1:
            threading.Thread(
                target=email_utils.send_moderation_digest, args=(pending,), daemon=True,
            ).start()
    except Exception as e:
        print(f"moderation digest check failed: {e}")


@router.get("/api/books/{hash_id}/comments")
async def get_comments(hash_id: str, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if not _is_admin(current_user) and _book_clearance(hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Guests (uid None) see only approved comments and own nothing.
    uid = current_user.id if current_user else None
    visible = or_(
        models.BookComment.status == "approved",
        models.BookComment.user_id == uid,
    )
    tops = db.query(models.BookComment).filter(
        models.BookComment.hash_id == hash_id,
        models.BookComment.parent_id.is_(None),
        visible,
    ).order_by(models.BookComment.created_at).all()

    replies_by_parent: dict[int, list] = {}
    top_ids = [c.id for c in tops]
    if top_ids:
        for r in db.query(models.BookComment).filter(
            models.BookComment.parent_id.in_(top_ids),
            visible,
        ).order_by(models.BookComment.created_at).all():
            replies_by_parent.setdefault(r.parent_id, []).append(r)

    # Author display names + each top-level author's own rating of this book.
    all_uids = {c.user_id for c in tops}
    for reps in replies_by_parent.values():
        all_uids.update(r.user_id for r in reps)
    names = {}
    if all_uids:
        names = {uid: _author_name(email, real_name) for uid, email, real_name in db.query(
            models.User.id, models.User.email, models.User.real_name
        ).filter(models.User.id.in_(all_uids)).all()}
    ratings = {}
    if tops:
        ratings = dict(db.query(models.BookRating.user_id, models.BookRating.rating).filter(
            models.BookRating.hash_id == hash_id,
            models.BookRating.user_id.in_([c.user_id for c in tops]),
        ).all())

    def node(c, with_rating: bool) -> dict:
        return {
            "id": c.id,
            "author_name": names.get(c.user_id, "user"),
            "body": c.body,
            "status": c.status,
            "created_at": c.created_at,
            "is_own": c.user_id == uid,
            "rating": ratings.get(c.user_id) if with_rating else None,
            "replies": [node(r, False) for r in replies_by_parent.get(c.id, [])],
        }

    return {"comments": [node(c, True) for c in tops]}


@router.post("/api/books/{hash_id}/comments")
async def create_comment(hash_id: str, payload: schemas.CommentCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin and _book_clearance(hash_id, db) > (current_user.clearance or 0):
        raise HTTPException(status_code=403, detail="Forbidden")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment body required")
    if len(body) > _COMMENT_MAX_LEN:
        raise HTTPException(status_code=400, detail="Comment is too long")
    if not db.query(models.Book.id).filter(models.Book.id == hash_id).first():
        raise HTTPException(status_code=404, detail="Book not found")

    if payload.parent_id is not None:
        parent = db.query(models.BookComment).filter(
            models.BookComment.id == payload.parent_id).first()
        if not parent or parent.hash_id != hash_id:
            raise HTTPException(status_code=400, detail="Invalid parent comment")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Cannot reply to a reply")
    else:
        existing = db.query(models.BookComment.id).filter(
            models.BookComment.hash_id == hash_id,
            models.BookComment.user_id == current_user.id,
            models.BookComment.parent_id.is_(None),
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="You have already commented on this book")

    now = _now_iso()
    # Admin comments need no moderation — they go live immediately.
    status = "approved" if current_user.is_admin else "pending"
    row = models.BookComment(
        user_id=current_user.id, hash_id=hash_id, parent_id=payload.parent_id,
        body=body, status=status, created_at=now, updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if row.status == "pending":
        _maybe_send_moderation_digest(db)
    return {"id": row.id, "status": row.status}


@router.put("/api/comments/{comment_id}")
async def edit_comment(comment_id: int, payload: schemas.CommentUpdate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")
    if row.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment body required")
    if len(body) > _COMMENT_MAX_LEN:
        raise HTTPException(status_code=400, detail="Comment is too long")
    row.body = body
    # Admin edits stay public; everyone else's edit returns to moderation.
    row.status = "approved" if current_user.is_admin else "pending"
    row.updated_at = _now_iso()
    db.commit()
    if row.status == "pending":
        _maybe_send_moderation_digest(db)
    return {"id": row.id, "status": row.status}


@router.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if not row:
        return {"message": "Removed"}
    if row.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    # FK enforcement (database.py) cascades replies (book_comments.parent_id)
    # when a top-level comment is deleted.
    db.delete(row)
    db.commit()
    return {"message": "Removed"}


# ---------- Admin: comment moderation ----------

@router.get("/api/admin/comments")
async def admin_list_comments(
    status: str = "pending",
    page: int = 1,
    per_page: int = 50,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))

    q = db.query(models.BookComment)
    if status == "pending":
        q = q.filter(models.BookComment.status == "pending")
    elif status in ("approved", "rejected"):
        q = q.filter(models.BookComment.status == status)
    # status == "recent" (or anything else): no status filter, newest first.

    total = q.order_by(None).count()
    rows = (
        q.order_by(models.BookComment.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    book_ids = {r.hash_id for r in rows}
    titles = dict(db.query(models.Book.id, models.Book.title).filter(
        models.Book.id.in_(book_ids)).all()) if book_ids else {}
    # One reachable path per book so the admin can open it from the queue.
    paths: dict[str, str] = {}
    if book_ids:
        for hid, sp in db.query(
            models.BookLocation.hash_id, models.BookLocation.symlink_path
        ).filter(models.BookLocation.hash_id.in_(book_ids)).all():
            paths.setdefault(hid, sp)
    user_ids = {r.user_id for r in rows}
    names = {uid: _author_name(email, real_name) for uid, email, real_name in db.query(
        models.User.id, models.User.email, models.User.real_name
    ).filter(models.User.id.in_(user_ids)).all()} if user_ids else {}
    parent_ids = {r.parent_id for r in rows if r.parent_id}
    parents = dict(db.query(models.BookComment.id, models.BookComment.body).filter(
        models.BookComment.id.in_(parent_ids)).all()) if parent_ids else {}

    return {
        "comments": [
            {
                "id": r.id,
                "hash_id": r.hash_id,
                "book_title": titles.get(r.hash_id),
                "book_path": paths.get(r.hash_id),
                "author_name": names.get(r.user_id, "user"),
                "body": r.body,
                "status": r.status,
                "parent_id": r.parent_id,
                "parent_snippet": (parents.get(r.parent_id) or "")[:120] if r.parent_id else None,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


@router.post("/api/admin/comments/{comment_id}/approve")
async def admin_approve_comment(comment_id: int, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")
    row.status = "approved"
    row.updated_at = _now_iso()
    author_email = db.query(models.User.email).filter(models.User.id == row.user_id).scalar()
    _audit(db, admin, "comment.moderate",
           target_kind="comment", target_id=row.id,
           summary=f"Approved comment by {author_email or f'user #{row.user_id}'}",
           details={"author": author_email or f"user #{row.user_id}",
                    "decision": "approve", "book_hash": row.hash_id})
    db.commit()
    return {"id": row.id, "status": row.status}


@router.delete("/api/admin/comments/{comment_id}")
async def admin_delete_comment(comment_id: int, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if row:
        author_id = row.user_id
        book_hash = row.hash_id
        author_email = db.query(models.User.email).filter(models.User.id == author_id).scalar()
        # FK enforcement cascades replies (book_comments.parent_id) on delete.
        db.delete(row)
        _audit(db, admin, "comment.moderate",
               target_kind="comment", target_id=comment_id,
               summary=f"Deleted comment by {author_email or f'user #{author_id}'}",
               details={"author": author_email or f"user #{author_id}",
                        "decision": "delete", "book_hash": book_hash})
        db.commit()
    return {"message": "Removed"}


# ==============================================================================
# Annotations (in-viewer highlights + notes)
# ==============================================================================
# A highlight is anchored to a text selection. Two visibility modes:
#   - private (is_public=0): only the author sees it, status is always 'approved'.
#   - public  (is_public=1): visible to other users only when status='approved'.
# Public annotations enter moderation as 'pending'; an admin promotes them. An
# author editing the body or anchor of a public annotation flips it back to
# 'pending'. Same clearance gate as comments — public notes on a restricted
# book are invisible to users without sufficient clearance.

_ANNOTATION_BODY_MAX_LEN = 5000
_ANNOTATION_SELECTED_MAX_LEN = 2000
_ANNOTATION_CONTEXT_MAX_LEN = 256


def _annotation_to_dict(row: models.Annotation, names: dict[int, str], viewer_id: int | None) -> dict:
    try:
        anchor = json.loads(row.anchor) if row.anchor else {}
    except json.JSONDecodeError:
        anchor = {}
    return {
        "id": row.id,
        "hash_id": row.hash_id,
        "author_id": row.user_id,
        "author_name": names.get(row.user_id, "user"),
        "anchor": anchor,
        "selected_text": row.selected_text,
        "text_prefix": row.text_prefix,
        "text_suffix": row.text_suffix,
        "body": row.body,
        "is_public": bool(row.is_public),
        "status": row.status,
        "is_own": row.user_id == viewer_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/api/books/{hash_id}/annotations")
async def get_annotations(
    hash_id: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if not _is_admin(current_user) and _book_clearance(hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")

    uid = current_user.id if current_user else None
    # Visible to viewer: own (any status) + others' public-and-approved.
    visible = and_(
        models.Annotation.is_public == True,  # noqa: E712
        models.Annotation.status == "approved",
    )
    if uid is not None:
        visible = or_(visible, models.Annotation.user_id == uid)

    rows = db.query(models.Annotation).filter(
        models.Annotation.hash_id == hash_id,
        visible,
    ).order_by(models.Annotation.created_at).all()

    author_ids = {r.user_id for r in rows}
    names: dict[int, str] = {}
    if author_ids:
        names = {
            u_id: _author_name(email, real_name)
            for u_id, email, real_name in db.query(
                models.User.id, models.User.email, models.User.real_name
            ).filter(models.User.id.in_(author_ids)).all()
        }

    return {"annotations": [_annotation_to_dict(r, names, uid) for r in rows]}


def _validate_annotation_payload(
    selected_text: str | None,
    body: str | None,
    text_prefix: str | None,
    text_suffix: str | None,
) -> None:
    if selected_text is not None:
        if not selected_text.strip():
            raise HTTPException(status_code=400, detail="Selection is empty")
        if len(selected_text) > _ANNOTATION_SELECTED_MAX_LEN:
            raise HTTPException(status_code=400, detail="Selection is too long")
    if body is not None and len(body) > _ANNOTATION_BODY_MAX_LEN:
        raise HTTPException(status_code=400, detail="Note is too long")
    if text_prefix is not None and len(text_prefix) > _ANNOTATION_CONTEXT_MAX_LEN:
        raise HTTPException(status_code=400, detail="Context prefix is too long")
    if text_suffix is not None and len(text_suffix) > _ANNOTATION_CONTEXT_MAX_LEN:
        raise HTTPException(status_code=400, detail="Context suffix is too long")


@router.post("/api/annotations")
async def create_annotation(
    payload: schemas.AnnotationCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.is_admin and _book_clearance(payload.hash_id, db) > (current_user.clearance or 0):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not db.query(models.Book.id).filter(models.Book.id == payload.hash_id).first():
        raise HTTPException(status_code=404, detail="Book not found")
    _validate_annotation_payload(payload.selected_text, payload.body, payload.text_prefix, payload.text_suffix)

    now = _now_iso()
    # Private annotations skip the queue; public ones from non-admins start pending.
    status_value = "approved" if (not payload.is_public or current_user.is_admin) else "pending"
    row = models.Annotation(
        user_id=current_user.id,
        hash_id=payload.hash_id,
        anchor=json.dumps(payload.anchor or {}, ensure_ascii=False),
        selected_text=payload.selected_text,
        text_prefix=payload.text_prefix,
        text_suffix=payload.text_suffix,
        body=(payload.body or None),
        is_public=bool(payload.is_public),
        status=status_value,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    names = {current_user.id: _author_name(current_user.email, current_user.real_name)}
    return _annotation_to_dict(row, names, current_user.id)


@router.put("/api/annotations/{annotation_id}")
async def update_annotation(
    annotation_id: int,
    payload: schemas.AnnotationUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(models.Annotation).filter(models.Annotation.id == annotation_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if row.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")

    _validate_annotation_payload(payload.selected_text, payload.body, payload.text_prefix, payload.text_suffix)

    content_changed = False
    if payload.anchor is not None:
        row.anchor = json.dumps(payload.anchor, ensure_ascii=False)
        content_changed = True
    if payload.selected_text is not None:
        row.selected_text = payload.selected_text
        content_changed = True
    if payload.text_prefix is not None:
        row.text_prefix = payload.text_prefix
    if payload.text_suffix is not None:
        row.text_suffix = payload.text_suffix
    if payload.body is not None:
        row.body = payload.body or None
        content_changed = True
    if payload.is_public is not None and bool(payload.is_public) != bool(row.is_public):
        row.is_public = bool(payload.is_public)
        content_changed = True

    # Public annotation edits by non-admins re-enter moderation.
    if row.is_public and content_changed and not current_user.is_admin:
        row.status = "pending"
    elif not row.is_public:
        row.status = "approved"

    row.updated_at = _now_iso()
    db.commit()
    db.refresh(row)
    names = {row.user_id: _author_name(current_user.email, current_user.real_name)} \
        if row.user_id == current_user.id else {}
    if row.user_id not in names:
        author = db.query(models.User).filter(models.User.id == row.user_id).first()
        if author:
            names[row.user_id] = _author_name(author.email, author.real_name)
    return _annotation_to_dict(row, names, current_user.id)


@router.delete("/api/annotations/{annotation_id}")
async def delete_annotation(
    annotation_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(models.Annotation).filter(models.Annotation.id == annotation_id).first()
    if not row:
        return {"message": "Removed"}
    if row.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    db.delete(row)
    db.commit()
    return {"message": "Removed"}


# ---------- Admin: annotation moderation ----------

@router.get("/api/admin/annotations")
async def admin_list_annotations(
    status: str = "pending",
    page: int = 1,
    per_page: int = 50,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))

    q = db.query(models.Annotation).filter(models.Annotation.is_public == True)  # noqa: E712
    if status in ("pending", "approved"):
        q = q.filter(models.Annotation.status == status)

    total = q.order_by(None).count()
    rows = (
        q.order_by(models.Annotation.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    book_ids = {r.hash_id for r in rows}
    titles = dict(db.query(models.Book.id, models.Book.title).filter(
        models.Book.id.in_(book_ids)).all()) if book_ids else {}
    paths: dict[str, str] = {}
    if book_ids:
        for hid, sp in db.query(
            models.BookLocation.hash_id, models.BookLocation.symlink_path
        ).filter(models.BookLocation.hash_id.in_(book_ids)).all():
            paths.setdefault(hid, sp)
    user_ids = {r.user_id for r in rows}
    names = {uid: _author_name(email, real_name) for uid, email, real_name in db.query(
        models.User.id, models.User.email, models.User.real_name
    ).filter(models.User.id.in_(user_ids)).all()} if user_ids else {}

    return {
        "annotations": [
            {
                "id": r.id,
                "hash_id": r.hash_id,
                "book_title": titles.get(r.hash_id),
                "book_path": paths.get(r.hash_id),
                "author_name": names.get(r.user_id, "user"),
                "selected_text": r.selected_text,
                "body": r.body,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


@router.post("/api/admin/annotations/{annotation_id}/approve")
async def admin_approve_annotation(
    annotation_id: int,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(models.Annotation).filter(models.Annotation.id == annotation_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Annotation not found")
    row.status = "approved"
    row.updated_at = _now_iso()
    author_email = db.query(models.User.email).filter(models.User.id == row.user_id).scalar()
    _audit(db, admin, "annotation.moderate",
           target_kind="annotation", target_id=row.id,
           summary=f"Approved annotation by {author_email or f'user #{row.user_id}'}",
           details={"author": author_email or f"user #{row.user_id}",
                    "decision": "approve", "book_hash": row.hash_id})
    db.commit()
    return {"id": row.id, "status": row.status}


@router.delete("/api/admin/annotations/{annotation_id}")
async def admin_delete_annotation(
    annotation_id: int,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(models.Annotation).filter(models.Annotation.id == annotation_id).first()
    if row:
        author_id = row.user_id
        book_hash = row.hash_id
        author_email = db.query(models.User.email).filter(models.User.id == author_id).scalar()
        db.delete(row)
        _audit(db, admin, "annotation.moderate",
               target_kind="annotation", target_id=annotation_id,
               summary=f"Deleted annotation by {author_email or f'user #{author_id}'}",
               details={"author": author_email or f"user #{author_id}",
                        "decision": "delete", "book_hash": book_hash})
        db.commit()
    return {"message": "Removed"}
