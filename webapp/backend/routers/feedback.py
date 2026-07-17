"""Router module (extracted from main.py): the feedback / contact-admin
subsystem — user→admin tickets with a status workflow, admin reply (+ internal
notes), attachments, the throttled per-recipient digest email, and the admin
settings panel. Moved verbatim from main.py (no logic change)."""
import os
import re
import uuid
import json
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, select

import models
import schemas
import email_utils
from database import get_db
from config import _now_iso, FEEDBACK_ATTACHMENT_DIR
from deps import get_current_user, require_admin
from serialize import _author_name

router = APIRouter()


_FEEDBACK_BODY_MAX_LEN = 4000
_FEEDBACK_SUBJECT_MAX_LEN = 200
_FEEDBACK_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
_FEEDBACK_ALLOWED_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_FEEDBACK_PUBLIC_ID_PREFIX = "feedback_public_id_seq"
_FEEDBACK_DIGEST_META_KEY = "feedback_email_last_sent_at"

_FEEDBACK_CATEGORIES = {"general", "bug", "feature", "book", "acquire", "other"}
_FEEDBACK_BOOK_SUBCATEGORIES = {"metadata", "corrupt", "copyright", "inappropriate", "duplicate"}
_FEEDBACK_URGENT_BOOK_SUBS = {"copyright", "inappropriate"}
_FEEDBACK_STATUSES = {"new", "open", "triage", "progress", "waiting", "resolved", "closed", "archived"}
_FEEDBACK_DIGEST_VALID_INTERVALS = {0, 1, 3, 6, 12, 24}
_FEEDBACK_DIGEST_VALID_BATCHES = {1, 3, 5}


def _feedback_settings(db: Session) -> models.AdminFeedbackSettings:
    """Always returns the singleton row — seeded on startup."""
    return db.query(models.AdminFeedbackSettings).filter(
        models.AdminFeedbackSettings.id == 1
    ).first()


def _next_feedback_public_id(db: Session) -> str:
    """Generate a per-year monotonic id like UL-2026-0421. Race-safe via a
    conditional UPDATE loop on app_meta — only one of any concurrent callers
    succeeds on each iteration."""
    year = datetime.now(timezone.utc).year
    key = f"{_FEEDBACK_PUBLIC_ID_PREFIX}:{year}"
    if not db.query(models.AppMeta).filter(models.AppMeta.key == key).first():
        try:
            db.add(models.AppMeta(key=key, value="0"))
            db.commit()
        except Exception:
            db.rollback()
    while True:
        current_val = db.query(models.AppMeta.value).filter(
            models.AppMeta.key == key
        ).scalar() or "0"
        current = int(current_val)
        target = str(current + 1)
        updated = db.query(models.AppMeta).filter(
            models.AppMeta.key == key,
            models.AppMeta.value == str(current),
        ).update({models.AppMeta.value: target}, synchronize_session=False)
        db.commit()
        if updated == 1:
            return f"UL-{year}-{current + 1:04d}"


def _diag_dict(payload_diag: dict | None) -> str | None:
    """Sanitize and JSON-stringify the client-supplied diagnostics. Caps each
    field at 200 chars; stores nothing about the request beyond what the
    client posted."""
    if not payload_diag:
        return None
    keep = {k: str(payload_diag.get(k, ""))[:200]
            for k in ("browser", "viewport", "route", "build", "locale")}
    return json.dumps(keep, ensure_ascii=False)


def _user_notif_prefs(db: Session, user_id: int) -> models.UserNotificationPrefs:
    row = db.query(models.UserNotificationPrefs).filter(
        models.UserNotificationPrefs.user_id == user_id
    ).first()
    if row:
        return row
    row = models.UserNotificationPrefs(
        user_id=user_id, email_on_reply=True, email_on_status=True,
        email_weekly_summary=False, updated_at=_now_iso(),
    )
    try:
        db.add(row); db.commit()
    except Exception:
        db.rollback()
        row = db.query(models.UserNotificationPrefs).filter(
            models.UserNotificationPrefs.user_id == user_id
        ).first()
    return row


def _resolve_thread_email_recipients(thread: models.FeedbackThread, db: Session) -> list[str]:
    """For directed threads: only the listed admins' emails. For broadcast
    threads: ADMIN_EMAIL + extra_recipients (no per-thread admins)."""
    rcpt_rows = db.query(models.User.email).join(
        models.FeedbackRecipient, models.FeedbackRecipient.admin_id == models.User.id,
    ).filter(models.FeedbackRecipient.thread_id == thread.id).all()
    if rcpt_rows:
        return [e for (e,) in rcpt_rows if e]
    return email_utils._resolve_admin_recipients()


def _thread_brief(t: models.FeedbackThread) -> dict:
    return {
        "public_id": t.public_id,
        "subject": t.subject,
        "category": t.category,
        "book_subcategory": t.book_subcategory,
    }


def _iso_add_hours(iso: str, hours: int) -> str:
    """Parse the ISO-8601 'Z' timestamps we store, add `hours`, format back."""
    try:
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso)
        dt = dt + timedelta(hours=hours)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return iso


def _maybe_send_feedback_digest(db: Session) -> None:
    """Throttled per-recipient digest. Race-safe via the same conditional
    UPDATE pattern as _maybe_send_moderation_digest."""
    try:
        settings = _feedback_settings(db)
        if not settings:
            return
        interval = settings.digest_interval_hours
        if interval <= 0:
            return
        pending = db.query(models.FeedbackThread).filter(
            models.FeedbackThread.status == "new",
            models.FeedbackThread.digested_at.is_(None),
        ).all()
        if len(pending) < settings.min_batch_size:
            return
        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        cutoff_iso = (now - timedelta(hours=interval)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not db.query(models.AppMeta).filter(models.AppMeta.key == _FEEDBACK_DIGEST_META_KEY).first():
            try:
                db.add(models.AppMeta(key=_FEEDBACK_DIGEST_META_KEY, value="1970-01-01T00:00:00Z"))
                db.commit()
            except Exception:
                db.rollback()
        updated = db.query(models.AppMeta).filter(
            models.AppMeta.key == _FEEDBACK_DIGEST_META_KEY,
            models.AppMeta.value < cutoff_iso,
        ).update({models.AppMeta.value: now_iso}, synchronize_session=False)
        db.commit()
        if updated != 1:
            return                                # lost the race or not yet eligible

        # We hold the slot — claim the threads so a concurrent caller can't
        # double-count them in the next interval window.
        for t in pending:
            t.digested_at = now_iso
        db.commit()

        # Partition per recipient.
        per_recipient: dict[str, list[dict]] = {}
        for t in pending:
            for to_email in _resolve_thread_email_recipients(t, db):
                per_recipient.setdefault(to_email, []).append(_thread_brief(t))

        threading.Thread(
            target=email_utils.send_feedback_digest_per_recipient,
            args=(per_recipient,), daemon=True,
        ).start()
    except Exception as e:
        print(f"feedback digest check failed: {e}")


def _maybe_email_user_update(thread: models.FeedbackThread, db: Session, change: str) -> None:
    """Per-event email to the thread's author, gated by their notification prefs.
    change ∈ {'reply', 'status'}."""
    try:
        prefs = _user_notif_prefs(db, thread.user_id)
        if change == "reply" and not prefs.email_on_reply:
            return
        if change == "status" and not prefs.email_on_status:
            return
        user = db.query(models.User).filter(models.User.id == thread.user_id).first()
        if not user or not user.email:
            return
        threading.Thread(
            target=email_utils.send_feedback_user_update,
            args=(user.email, thread.public_id, thread.subject, thread.status, change),
            daemon=True,
        ).start()
    except Exception as e:
        print(f"user feedback notification failed: {e}")


def _book_summary_for_thread(thread: models.FeedbackThread, db: Session) -> tuple[Optional[str], Optional[str]]:
    """Return (title, first_symlink_path) for a thread's attached book, or
    (None, None) when no book is attached."""
    if not thread.book_hash_id:
        return None, None
    book = db.query(models.Book).filter(models.Book.id == thread.book_hash_id).first()
    if not book:
        return None, None
    loc = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == thread.book_hash_id
    ).first()
    return (book.title or book.original_filename, loc[0] if loc else None)


def _resolve_thread_recipients_briefs(
    thread_id: int, db: Session, viewer: models.User,
) -> list[dict]:
    """Return the recipient list for a thread, as briefs for the API
    payload (id/name/is_you)."""
    rows = db.query(models.User.id, models.User.email, models.User.real_name).join(
        models.FeedbackRecipient, models.FeedbackRecipient.admin_id == models.User.id,
    ).filter(models.FeedbackRecipient.thread_id == thread_id).all()
    return [
        {
            "id": uid,
            "name": _author_name(email, real_name),
            "is_you": uid == viewer.id,
        }
        for uid, email, real_name in rows
    ]


def _summarise_thread(thread: models.FeedbackThread, db: Session, viewer: models.User) -> dict:
    """Build the row shape for list endpoints. `has_unread` flips true when
    the most recent non-internal message wasn't authored by the viewer."""
    author = db.query(models.User).filter(models.User.id == thread.user_id).first()
    book_title, book_path = _book_summary_for_thread(thread, db)
    recipients = _resolve_thread_recipients_briefs(thread.id, db, viewer)

    reply_count = db.query(func.count(models.FeedbackMessage.id)).filter(
        models.FeedbackMessage.thread_id == thread.id,
        models.FeedbackMessage.kind.in_(("message", "admin")),
    ).scalar() or 0
    if reply_count > 0:
        reply_count = max(0, reply_count - 1)   # subtract the original 'message' row

    # has_unread: latest non-internal message not authored by viewer.
    last_msg = db.query(models.FeedbackMessage).filter(
        models.FeedbackMessage.thread_id == thread.id,
        models.FeedbackMessage.kind.in_(("message", "admin")),
    ).order_by(models.FeedbackMessage.id.desc()).first()
    has_unread = bool(last_msg and last_msg.author_id != viewer.id)

    return {
        "id": thread.id,
        "public_id": thread.public_id,
        "user_name": _author_name(author.email if author else "", author.real_name if author else None),
        "user_email": (author.email if author else "") if viewer.is_admin else "",
        "category": thread.category,
        "book_subcategory": thread.book_subcategory,
        "subject": thread.subject,
        "status": thread.status,
        "book_hash_id": thread.book_hash_id,
        "book_title": book_title,
        "book_path": book_path,
        "recipients": recipients,
        "is_broadcast": len(recipients) == 0,
        "reply_count": reply_count,
        "has_unread": has_unread,
        "archived": bool(thread.archived_at),
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
    }


def _expand_thread(thread: models.FeedbackThread, db: Session, viewer: models.User) -> dict:
    """Detail payload. Filters internal messages and diag for non-admin viewers
    — the single defense-in-depth chokepoint for the two privacy risks."""
    base = _summarise_thread(thread, db, viewer)
    is_admin_view = bool(viewer.is_admin)

    # Messages with author lookup. Filter 'internal' for non-admin viewers.
    msgs = db.query(models.FeedbackMessage).filter(
        models.FeedbackMessage.thread_id == thread.id
    ).order_by(models.FeedbackMessage.id.asc()).all()

    # Author lookup map for the message list.
    author_ids = {m.author_id for m in msgs}
    authors = {
        u.id: u for u in db.query(models.User).filter(models.User.id.in_(author_ids)).all()
    }

    messages: list[dict] = []
    body_text = ""
    seen_first_message = False
    for m in msgs:
        if m.kind == "internal" and not is_admin_view:
            continue
        u = authors.get(m.author_id)
        is_admin_author = bool(u.is_admin) if u else False
        messages.append({
            "id": m.id,
            "kind": m.kind,
            "author_name": _author_name(u.email if u else "", u.real_name if u else None) if u else "user",
            "is_admin": is_admin_author,
            "is_own": m.author_id == viewer.id,
            "body": m.body,
            "created_at": m.created_at,
        })
        if m.kind == "message" and not seen_first_message:
            body_text = m.body
            seen_first_message = True

    # Attachment (one max). URL is admin-or-author-gated by the route.
    att_row = db.query(models.FeedbackAttachment).filter(
        models.FeedbackAttachment.thread_id == thread.id
    ).first()
    attachment = None
    if att_row:
        attachment = {
            "filename": att_row.filename,
            "url": f"/api/feedback/{thread.id}/attachment/{att_row.filename}",
            "bytes": att_row.bytes,
            "content_type": att_row.content_type,
        }

    # diag: parsed JSON only for admin viewers.
    diag_obj = None
    if is_admin_view and thread.diag:
        try:
            diag_obj = json.loads(thread.diag)
        except Exception:
            diag_obj = None

    # assigned_admin_name
    assigned_admin_name = None
    if thread.assigned_admin_id:
        a = db.query(models.User).filter(models.User.id == thread.assigned_admin_id).first()
        if a:
            assigned_admin_name = _author_name(a.email, a.real_name)

    base.update({
        "body": body_text,
        "book_page": thread.book_page,
        "diag": diag_obj,
        "attachment": attachment,
        "messages": messages,
        "assigned_admin_name": assigned_admin_name,
    })
    return base


def _status_counts(db: Session, viewer: models.User) -> dict:
    """All status counts (visibility-filtered) + 'mine' + 'all'. Used by the
    admin inbox toolbar to render badges."""
    broadcast_subq = ~db.query(models.FeedbackRecipient.thread_id).filter(
        models.FeedbackRecipient.thread_id == models.FeedbackThread.id
    ).exists()
    my_threads = select(models.FeedbackRecipient.thread_id).where(
        models.FeedbackRecipient.admin_id == viewer.id
    )
    visible = or_(broadcast_subq, models.FeedbackThread.id.in_(my_threads))

    counts: dict[str, int] = {}
    for st in _FEEDBACK_STATUSES:
        counts[st] = db.query(func.count(models.FeedbackThread.id)).filter(
            visible, models.FeedbackThread.status == st,
        ).scalar() or 0
    counts["all"] = db.query(func.count(models.FeedbackThread.id)).filter(visible).scalar() or 0
    counts["mine"] = db.query(func.count(models.FeedbackThread.id)).filter(
        models.FeedbackThread.id.in_(
            select(models.FeedbackRecipient.thread_id).where(
                models.FeedbackRecipient.admin_id == viewer.id
            )
        )
    ).scalar() or 0
    return counts


# ---------- User-side routes ----------

@router.post("/api/feedback")
async def create_feedback(
    payload: schemas.FeedbackCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.category not in _FEEDBACK_CATEGORIES:
        raise HTTPException(status_code=400, detail="invalid category")
    if payload.category == "book":
        if payload.book_subcategory not in _FEEDBACK_BOOK_SUBCATEGORIES:
            raise HTTPException(status_code=400, detail="book category requires a valid book_subcategory")
    subject = (payload.subject or "").strip()
    body = (payload.body or "").strip()
    if not subject or not body:
        raise HTTPException(status_code=400, detail="subject and body required")
    if len(subject) > _FEEDBACK_SUBJECT_MAX_LEN or len(body) > _FEEDBACK_BODY_MAX_LEN:
        raise HTTPException(status_code=400, detail="too long")

    book_hash = payload.book_hash_id
    if book_hash and not db.query(models.Book.id).filter(models.Book.id == book_hash).first():
        raise HTTPException(status_code=400, detail="unknown book")

    # Recipient targeting: only honour the field when the sender is admin.
    recipient_ids: list[int] = []
    if current_user.is_admin and payload.recipient_admin_ids:
        valid_ids = {
            uid for (uid,) in db.query(models.User.id).filter(
                models.User.is_admin == True,                                # noqa: E712
                models.User.is_active == True,                                # noqa: E712
                models.User.id.in_(payload.recipient_admin_ids),
            ).all()
        }
        # Preserve client order, drop invalid, de-dup.
        seen: set[int] = set()
        for uid in payload.recipient_admin_ids:
            if uid in valid_ids and uid not in seen:
                recipient_ids.append(uid)
                seen.add(uid)
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="no valid recipients")

    now = _now_iso()
    thread = models.FeedbackThread(
        public_id=_next_feedback_public_id(db),
        user_id=current_user.id,
        category=payload.category,
        book_subcategory=payload.book_subcategory if payload.category == "book" else None,
        subject=subject,
        status="new",
        book_hash_id=book_hash,
        book_page=payload.book_page,
        diag=_diag_dict(payload.diag),
        created_at=now, updated_at=now,
    )
    db.add(thread)
    db.flush()
    db.add(models.FeedbackMessage(
        thread_id=thread.id, author_id=current_user.id,
        kind="message", body=body, created_at=now,
    ))
    for aid in recipient_ids:
        db.add(models.FeedbackRecipient(thread_id=thread.id, admin_id=aid))
    db.commit()
    db.refresh(thread)

    settings = _feedback_settings(db)
    is_urgent = bool(
        settings and settings.urgent_bypass
        and thread.book_subcategory in _FEEDBACK_URGENT_BOOK_SUBS
    )
    if is_urgent:
        threading.Thread(
            target=email_utils.send_feedback_urgent,
            args=(thread.public_id, thread.subject, thread.book_subcategory,
                  _resolve_thread_email_recipients(thread, db)),
            daemon=True,
        ).start()
    else:
        _maybe_send_feedback_digest(db)

    return {"id": thread.id, "public_id": thread.public_id}


@router.post("/api/feedback/{thread_id}/attachment")
async def upload_feedback_attachment(
    thread_id: int,
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    thread = db.query(models.FeedbackThread).filter(models.FeedbackThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="not found")
    if thread.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not file.content_type or file.content_type not in _FEEDBACK_ALLOWED_MIME:
        raise HTTPException(status_code=400, detail="Unsupported image type")

    # Chunked read so a malicious 1GB POST can't OOM uvicorn before the cap
    # check fires. The previous `await file.read()` slurped the whole body
    # first and then checked the size — safe only while nginx capped requests
    # at 100M, which it no longer does.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _FEEDBACK_ATTACHMENT_MAX_BYTES:
            raise HTTPException(status_code=400, detail="file too large")
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="empty file")
    data = b"".join(chunks)

    # One attachment per thread — replace any existing.
    existing = db.query(models.FeedbackAttachment).filter(
        models.FeedbackAttachment.thread_id == thread.id
    ).first()
    if existing:
        old_full = os.path.join(FEEDBACK_ATTACHMENT_DIR, existing.stored_path)
        if os.path.isfile(old_full):
            try:
                os.remove(old_full)
            except Exception:
                pass
        db.delete(existing)

    safe_orig = os.path.basename(file.filename or "screenshot")
    ext = safe_orig.rsplit(".", 1)[-1].lower() if "." in safe_orig else "png"
    if ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
        ext = "png"
    stored = f"{thread.public_id}_{uuid.uuid4().hex[:8]}.{ext}"
    full = os.path.join(FEEDBACK_ATTACHMENT_DIR, stored)
    with open(full, "wb") as fh:
        fh.write(data)

    now = _now_iso()
    db.add(models.FeedbackAttachment(
        thread_id=thread.id, filename=safe_orig, stored_path=stored,
        content_type=file.content_type, bytes=len(data), created_at=now,
    ))
    thread.updated_at = now
    db.commit()
    return {"ok": True, "filename": safe_orig, "bytes": len(data)}


@router.get("/api/feedback/{thread_id}/attachment/{filename}")
async def get_feedback_attachment(
    thread_id: int, filename: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    thread = db.query(models.FeedbackThread).filter(models.FeedbackThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="not found")
    if not (current_user.is_admin or thread.user_id == current_user.id):
        raise HTTPException(status_code=403, detail="Forbidden")
    att = db.query(models.FeedbackAttachment).filter(
        models.FeedbackAttachment.thread_id == thread.id,
        models.FeedbackAttachment.filename == filename,
    ).first()
    if not att:
        raise HTTPException(status_code=404, detail="not found")
    full = os.path.join(FEEDBACK_ATTACHMENT_DIR, att.stored_path)
    # Defense in depth: stored_path must stay under FEEDBACK_ATTACHMENT_DIR.
    if not os.path.abspath(full).startswith(os.path.abspath(FEEDBACK_ATTACHMENT_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(full, media_type=att.content_type, filename=att.filename)


@router.get("/api/feedback")
async def list_my_feedback(
    status: str = "all", page: int = 1, per_page: int = 50,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))
    q = db.query(models.FeedbackThread).filter(models.FeedbackThread.user_id == current_user.id)
    if status != "all":
        if status not in _FEEDBACK_STATUSES:
            raise HTTPException(status_code=400, detail="invalid status filter")
        q = q.filter(models.FeedbackThread.status == status)
    total = q.count()
    rows = q.order_by(models.FeedbackThread.updated_at.desc()) \
            .limit(per_page).offset((page - 1) * per_page).all()
    return {"total": total, "items": [_summarise_thread(r, db, viewer=current_user) for r in rows]}


@router.get("/api/feedback/{thread_ref}")
async def get_feedback_thread(
    thread_ref: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """thread_ref accepts either the numeric id or the public_id (UL-YYYY-NNNN)."""
    thread = None
    if thread_ref.isdigit():
        thread = db.query(models.FeedbackThread).filter(
            models.FeedbackThread.id == int(thread_ref)
        ).first()
    if thread is None:
        thread = db.query(models.FeedbackThread).filter(
            models.FeedbackThread.public_id == thread_ref
        ).first()
    if not thread:
        raise HTTPException(status_code=404, detail="not found")

    is_admin_view = bool(current_user.is_admin)
    if not is_admin_view and thread.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # If admin (not the author) is reading a directed thread they're not on, hide it.
    if is_admin_view and thread.user_id != current_user.id:
        rcpt_rows = db.query(models.FeedbackRecipient.admin_id).filter(
            models.FeedbackRecipient.thread_id == thread.id
        ).all()
        if rcpt_rows:                                # directed
            allowed = {r[0] for r in rcpt_rows}
            if current_user.id not in allowed:
                raise HTTPException(status_code=403, detail="Forbidden")

    return _expand_thread(thread, db, viewer=current_user)


@router.post("/api/feedback/{thread_id}/reply")
async def reply_to_feedback(
    thread_id: int,
    payload: schemas.FeedbackReply,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    thread = db.query(models.FeedbackThread).filter(models.FeedbackThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="not found")
    is_admin_actor = bool(current_user.is_admin)
    is_author = thread.user_id == current_user.id
    if not (is_admin_actor or is_author):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Admin not-the-author replying to a directed thread they're not on: blocked.
    if is_admin_actor and not is_author:
        rcpt_rows = db.query(models.FeedbackRecipient.admin_id).filter(
            models.FeedbackRecipient.thread_id == thread.id
        ).all()
        if rcpt_rows:
            allowed = {r[0] for r in rcpt_rows}
            if current_user.id not in allowed:
                raise HTTPException(status_code=403, detail="Forbidden")

    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body required")
    if len(body) > _FEEDBACK_BODY_MAX_LEN:
        raise HTTPException(status_code=400, detail="too long")

    now = _now_iso()
    if is_admin_actor:
        kind = "internal" if payload.internal else "admin"
    else:
        kind = "message"

    db.add(models.FeedbackMessage(
        thread_id=thread.id, author_id=current_user.id,
        kind=kind, body=body, created_at=now,
    ))

    status_flipped = False
    # User reply on resolved/closed → re-open.
    if not is_admin_actor and thread.status in ("resolved", "closed"):
        if thread.status != "open":
            db.add(models.FeedbackMessage(
                thread_id=thread.id, author_id=current_user.id,
                kind="status", body="open", created_at=now,
            ))
            thread.status = "open"
            thread.archived_at = None
            status_flipped = True
    # Admin can flip status atomically.
    if is_admin_actor and payload.new_status:
        if payload.new_status not in _FEEDBACK_STATUSES:
            raise HTTPException(status_code=400, detail="invalid status")
        if payload.new_status != thread.status:
            db.add(models.FeedbackMessage(
                thread_id=thread.id, author_id=current_user.id,
                kind="status", body=payload.new_status, created_at=now,
            ))
            thread.status = payload.new_status
            if payload.new_status != "archived":
                thread.archived_at = None
            status_flipped = True

    thread.updated_at = now
    db.commit()

    if is_admin_actor:
        # Internal notes are invisible to the user, so they never trigger a
        # reply email — but a status flip is user-visible (a `status` message
        # is persisted above) regardless of the note's kind, so its email must
        # not be gated on the kind.
        if kind != "internal":
            _maybe_email_user_update(thread, db, change="reply")
        if status_flipped:
            _maybe_email_user_update(thread, db, change="status")
    # User replies don't trigger an admin email — admins see updates on next inbox load.

    return {"ok": True}


@router.post("/api/feedback/{thread_id}/resolve")
async def resolve_feedback(
    thread_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Author-only quick resolve: flips status to 'resolved' without writing
    a message body. Admin equivalent is the status PATCH route."""
    thread = db.query(models.FeedbackThread).filter(models.FeedbackThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="not found")
    if thread.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if thread.status == "resolved":
        return {"ok": True}
    now = _now_iso()
    db.add(models.FeedbackMessage(
        thread_id=thread.id, author_id=current_user.id,
        kind="status", body="resolved", created_at=now,
    ))
    thread.status = "resolved"
    thread.updated_at = now
    db.commit()
    return {"ok": True}


# ---------- Admin-side routes ----------

@router.get("/api/admins")
async def list_admins(
    _admin: models.User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Used by the compose page's recipient picker. Admin-only."""
    rows = db.query(models.User.id, models.User.email, models.User.real_name) \
             .filter(models.User.is_admin == True, models.User.is_active == True) \
             .order_by(models.User.email).all()
    return {"items": [
        {"id": uid, "name": _author_name(email, real_name), "email": email}
        for uid, email, real_name in rows
    ]}


@router.get("/api/admin/feedback")
async def admin_list_feedback(
    status: str = "new", page: int = 1, per_page: int = 50, q: str = "",
    _admin: models.User = Depends(require_admin), db: Session = Depends(get_db),
):
    """List threads for the admin inbox. 'mine' returns directed-to-me threads;
    other filters return broadcast OR directed-to-me, hiding directed-to-other."""
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))
    query = db.query(models.FeedbackThread)

    my_threads = select(models.FeedbackRecipient.thread_id).where(
        models.FeedbackRecipient.admin_id == _admin.id
    )

    if status == "mine":
        query = query.filter(models.FeedbackThread.id.in_(my_threads))
    else:
        if status != "all":
            if status not in _FEEDBACK_STATUSES:
                raise HTTPException(status_code=400, detail="invalid status")
            query = query.filter(models.FeedbackThread.status == status)
        broadcast_subq = ~db.query(models.FeedbackRecipient.thread_id).filter(
            models.FeedbackRecipient.thread_id == models.FeedbackThread.id
        ).exists()
        query = query.filter(or_(broadcast_subq, models.FeedbackThread.id.in_(my_threads)))

    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            models.FeedbackThread.subject.like(like),
            models.FeedbackThread.public_id.like(like),
        ))
    total = query.count()
    rows = query.order_by(models.FeedbackThread.updated_at.desc()) \
                .limit(per_page).offset((page - 1) * per_page).all()
    return {
        "total": total,
        "counts": _status_counts(db, viewer=_admin),
        "items": [_summarise_thread(r, db, viewer=_admin) for r in rows],
    }


@router.patch("/api/admin/feedback/{thread_id}/status")
async def admin_set_feedback_status(
    thread_id: int, payload: schemas.FeedbackStatusUpdate,
    _admin: models.User = Depends(require_admin), db: Session = Depends(get_db),
):
    thread = db.query(models.FeedbackThread).filter(models.FeedbackThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="not found")
    if payload.status not in _FEEDBACK_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    now = _now_iso()
    status_changed = payload.status != thread.status
    if status_changed:
        db.add(models.FeedbackMessage(
            thread_id=thread.id, author_id=_admin.id,
            kind="status", body=payload.status, created_at=now,
        ))
    thread.status = payload.status
    thread.archived_at = now if payload.archive else None
    thread.updated_at = now
    db.commit()
    if status_changed:
        _maybe_email_user_update(thread, db, change="status")
    return {"ok": True}


@router.post("/api/admin/feedback/{thread_id}/assign")
async def admin_assign_feedback(
    thread_id: int, payload: schemas.FeedbackAssign,
    _admin: models.User = Depends(require_admin), db: Session = Depends(get_db),
):
    thread = db.query(models.FeedbackThread).filter(models.FeedbackThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="not found")
    if payload.assignee_id is not None:
        ok = db.query(models.User.id).filter(
            models.User.id == payload.assignee_id,
            models.User.is_admin == True,                                     # noqa: E712
            models.User.is_active == True,                                    # noqa: E712
        ).first()
        if not ok:
            raise HTTPException(status_code=400, detail="invalid assignee")
    thread.assigned_admin_id = payload.assignee_id
    thread.updated_at = _now_iso()
    db.commit()
    return {"ok": True}


@router.delete("/api/admin/feedback/{thread_id}")
async def admin_delete_feedback(
    thread_id: int,
    _admin: models.User = Depends(require_admin), db: Session = Depends(get_db),
):
    thread = db.query(models.FeedbackThread).filter(models.FeedbackThread.id == thread_id).first()
    if not thread:
        return {"message": "removed"}
    # Clean up attachments on disk first.
    for att in db.query(models.FeedbackAttachment).filter(
        models.FeedbackAttachment.thread_id == thread.id
    ).all():
        full = os.path.join(FEEDBACK_ATTACHMENT_DIR, att.stored_path)
        if os.path.isfile(full):
            try:
                os.remove(full)
            except Exception:
                pass
    # FK enforcement (database.py) cascades messages/recipients/attachments when
    # the thread row is deleted; the attachment files on disk were removed above.
    db.delete(thread)
    db.commit()
    return {"message": "removed"}


@router.get("/api/admin/feedback/settings")
async def admin_get_feedback_settings(
    _admin: models.User = Depends(require_admin), db: Session = Depends(get_db),
):
    s = _feedback_settings(db)
    last = db.query(models.AppMeta.value).filter(
        models.AppMeta.key == _FEEDBACK_DIGEST_META_KEY
    ).scalar()
    if last == "1970-01-01T00:00:00Z":
        last = None
    pending = db.query(func.count(models.FeedbackThread.id)).filter(
        models.FeedbackThread.status == "new",
        models.FeedbackThread.digested_at.is_(None),
    ).scalar() or 0
    next_eligible = None
    if last and s.digest_interval_hours > 0:
        next_eligible = _iso_add_hours(last, s.digest_interval_hours)
    return {
        "digest_interval_hours": s.digest_interval_hours,
        "min_batch_size": s.min_batch_size,
        "urgent_bypass": bool(s.urgent_bypass),
        "extra_recipients": [e for e in (s.extra_recipients or "").split(",") if e.strip()],
        "last_digest_at": last,
        "next_eligible_at": next_eligible,
        "pending_count": pending,
    }


@router.put("/api/admin/feedback/settings")
async def admin_set_feedback_settings(
    payload: schemas.AdminFeedbackSettingsPayload,
    _admin: models.User = Depends(require_admin), db: Session = Depends(get_db),
):
    if payload.digest_interval_hours not in _FEEDBACK_DIGEST_VALID_INTERVALS:
        raise HTTPException(status_code=400, detail="invalid interval")
    if payload.min_batch_size not in _FEEDBACK_DIGEST_VALID_BATCHES:
        raise HTTPException(status_code=400, detail="invalid batch size")
    # Sanitize extra recipients — lowercase, strip whitespace, drop blanks, basic shape check.
    cleaned: list[str] = []
    seen: set[str] = set()
    email_re = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    for raw in payload.extra_recipients:
        e = (raw or "").strip().lower()
        if not e:
            continue
        if not email_re.match(e):
            raise HTTPException(status_code=400, detail=f"invalid email: {raw}")
        if e not in seen:
            cleaned.append(e); seen.add(e)
    s = _feedback_settings(db)
    s.digest_interval_hours = payload.digest_interval_hours
    s.min_batch_size = payload.min_batch_size
    s.urgent_bypass = payload.urgent_bypass
    s.extra_recipients = ",".join(cleaned)
    s.updated_at = _now_iso()
    db.commit()
    return {"ok": True}


@router.post("/api/admin/feedback/digest/force")
async def admin_force_feedback_digest(
    _admin: models.User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Force-send the digest now, bypassing the throttle. Resets the timer
    so the next normal digest waits the full interval."""
    pending = db.query(models.FeedbackThread).filter(
        models.FeedbackThread.status == "new",
        models.FeedbackThread.digested_at.is_(None),
    ).all()
    if not pending:
        raise HTTPException(status_code=400, detail="nothing pending")
    now_iso = _now_iso()
    for t in pending:
        t.digested_at = now_iso
    # Reset the throttle row (insert-or-update).
    if not db.query(models.AppMeta).filter(models.AppMeta.key == _FEEDBACK_DIGEST_META_KEY).first():
        db.add(models.AppMeta(key=_FEEDBACK_DIGEST_META_KEY, value=now_iso))
    else:
        db.query(models.AppMeta).filter(
            models.AppMeta.key == _FEEDBACK_DIGEST_META_KEY
        ).update({models.AppMeta.value: now_iso}, synchronize_session=False)
    db.commit()

    per_recipient: dict[str, list[dict]] = {}
    for t in pending:
        for to_email in _resolve_thread_email_recipients(t, db):
            per_recipient.setdefault(to_email, []).append(_thread_brief(t))
    threading.Thread(
        target=email_utils.send_feedback_digest_per_recipient,
        args=(per_recipient,), daemon=True,
    ).start()
    return {"ok": True, "sent": len(pending)}

