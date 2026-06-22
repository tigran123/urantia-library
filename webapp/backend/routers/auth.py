"""Router module (extracted from main.py): authentication, registration,
onboarding, legal acceptance, the signed-in user's own profile/settings/avatar,
and the GDPR "My Activity" surface. Also serves /api/library-stats (the footer
counts + presence). Moved verbatim from main.py (no logic change).

`create_access_token` returns `(token, jti, expires_at)`; /api/login registers
the jti in `state._active_sessions` and mirrors it to auth_sessions via
`_persist_session` — keep that logic identical."""
import os
import io
import uuid
import json
import asyncio
import logging
import time
import hashlib
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request, Depends, Cookie, Response, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from PIL import Image, ImageOps

import models
import schemas
import state as state
import email_utils
from database import get_db, SessionLocal, LEGAL_VERSION
from security import (
    get_password_hash, verify_password, create_access_token,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from config import (
    _AVATAR_MAX_BYTES, ONLINE_WINDOW, _now_iso,
    _AUDIO_EXTS, _VIDEO_EXTS, MIN_PASSWORD_LENGTH,
    RESET_RL_MAX, RESET_RL_WINDOW_S,
)
from deps import (
    get_current_user, get_optional_user, _maybe_refresh_session,
    _clearance_of, _decode_claims,
)
from background import _persist_session, _record_usage_event, _audit

router = APIRouter()


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect the
    avatar dir via `monkeypatch.setattr(main, "AVATAR_DIR", ...)` and patch
    `main._delete_session` (see tests/test_avatar_upload.py /
    tests/test_auth_sessions.py), so those are read through `main` (the patch
    target) at call time rather than bound at import. main re-exports AVATAR_DIR
    from config and _delete_session from background, so production reads the same
    values. Call-time import: no load-time cycle."""
    import main
    return main


@router.post("/api/login")
async def login(request: Request, login_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == login_data.email).first()
    if not user or not verify_password(login_data.password, user.hashed_password):
        _record_usage_event(
            request, "login", user=user,
            extra={"success": False, "email": login_data.email, "reason": "bad_credentials"},
        )
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not user.is_active:
        _record_usage_event(
            request, "login", user=user,
            extra={"success": False, "email": login_data.email, "reason": "inactive"},
        )
        raise HTTPException(status_code=400, detail="User is not active")

    access_token, jti, expires_at = create_access_token(data={"sub": user.email})
    now = datetime.now(timezone.utc)
    sess = {
        "user_id": user.id,
        "email": user.email,
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "created_at": now,
        "last_seen_at": now,
        "expires_at": expires_at,
    }
    with state._active_sessions_lock:
        state._purge_expired_sessions_locked()
        state._active_sessions[jti] = sess
    # Mirror to auth_sessions so the session survives a restart. Outside the
    # lock — never hold the threading lock across DB I/O. No expiry purge here:
    # expired rows are only ever read at startup, where _load_active_sessions
    # sweeps them, so purging on the login hot path would be wasted work.
    _persist_session(db, jti, sess)
    response = JSONResponse(content={"message": "Login successful"})
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() != "false",
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    _record_usage_event(request, "login", user=user, extra={"success": True})
    return response

@router.post("/api/logout")
async def logout(access_token: str | None = Cookie(None)):
    response = JSONResponse(content={"message": "Logout successful"})
    # Cookie attributes must match those used in /api/login, otherwise the
    # browser ignores the Set-Cookie that's meant to clear it and the JWT
    # keeps validating — which would leave the user counted as "online" until
    # the 5 min idle window elapses (or forever, if their tab keeps polling).
    response.delete_cookie(
        key="access_token",
        path="/",
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() != "false",
        samesite="lax",
    )
    # Decode the cookie rather than going through get_optional_user — an
    # already-terminated session must still be able to clear its own row, and the
    # dep would refuse it. verify_exp=False so an *expired* cookie still yields
    # its jti (exp validity is irrelevant when tearing a session down); the
    # signature is still verified, so a tampered token decodes to {}.
    if access_token:
        claims = _decode_claims(access_token, verify_exp=False)
        jti = claims.get("jti")
        email = claims.get("sub")
        if jti:
            # Delete the persisted row first, then evict from memory — and only
            # if the delete succeeded. A session gone from memory but still on
            # disk would be resurrected by _load_active_sessions on the next
            # restart. If the delete fails we leave both in place (a consistent,
            # still-live state); the cleared cookie has already logged out this
            # browser, and the row is reaped later by the expiry purge.
            db_sess = SessionLocal()
            try:
                _m()._delete_session(db_sess, jti)
                with state._active_sessions_lock:
                    state._active_sessions.pop(jti, None)
            except Exception:
                logging.exception("logout: failed to delete persisted session jti=%s", jti)
            finally:
                db_sess.close()
        if email:
            db_local = SessionLocal()
            try:
                u = db_local.query(models.User).filter(models.User.email == email).first()
                if u is not None:
                    with state._last_seen_lock:
                        state._last_seen.pop(u.id, None)
            finally:
                db_local.close()
    return response

def _user_response_dict(u: models.User) -> dict:
    """Serialize a User row into the shape returned by /api/me, /api/legal/accept,
    /api/users/me/settings, and /api/users/me/avatar. Kept here as a single
    source of truth so adding a field doesn't drift between four endpoints."""
    return {
        "email": u.email,
        "avatar_url": u.avatar_url,
        "real_name": u.real_name,
        "search_per_page": u.search_per_page,
        "is_admin": bool(u.is_admin),
        "clearance": int(u.clearance or 0),
        "legal_version_accepted": u.legal_version_accepted,
        "legal_acceptance_current": u.legal_version_accepted == LEGAL_VERSION,
    }


@router.get("/api/me", response_model=schemas.UserResponse)
async def get_me(
    response: Response,
    access_token: str | None = Cookie(None),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _maybe_refresh_session(response, access_token, current_user, db)
    return _user_response_dict(current_user)


# ==============================================================================
# My Activity — GDPR subject-rights surface for the signed-in user
# ==============================================================================
# Backs the #/account/activity page. GET returns the user's own usage_events,
# either paginated (default) or as a full JSON export (?format=json).
# DELETE wipes all events for this user — exercises Art. 17 ("right to be
# forgotten") for the data subject. Guest erasure is handled out-of-band via
# the contact-admin path documented in the Privacy Policy.

@router.get("/api/me/activity")
async def my_activity(
    page: int = 1,
    per_page: int | None = None,
    format: str | None = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base = (
        db.query(models.UsageEvent)
          .filter(models.UsageEvent.user_id == current_user.id)
          .order_by(models.UsageEvent.id.desc())
    )

    # Art. 20 portability export: all rows in one response, no pagination.
    if format == "json":
        rows = base.all()
        return {
            "exported_at": _now_iso(),
            "user_email": current_user.email,
            "events": [_my_activity_row(ev) for ev in rows],
        }

    # Default to the user's own "results per page" preference (the same knob
    # the Search results use), with a 50-row fallback when unset.
    if per_page is None:
        per_page = int(current_user.search_per_page or 50)
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))
    total = base.count()
    rows = base.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "page": page,
        "per_page": per_page,
        "total": int(total),
        "total_pages": (int(total) + per_page - 1) // per_page,
        "events": [_my_activity_row(ev) for ev in rows],
    }


def _my_activity_row(ev: models.UsageEvent) -> dict:
    extra = None
    if ev.extra_json:
        try:
            extra = json.loads(ev.extra_json)
        except json.JSONDecodeError:
            extra = {"_raw": ev.extra_json}
    return {
        "id": ev.id,
        "ts": ev.ts,
        "kind": ev.kind,
        "path": ev.path,
        "hash_id": ev.hash_id,
        "ip": ev.ip,
        "user_agent": ev.user_agent,
        "geo_country": ev.geo_country,
        "geo_city": ev.geo_city,
        "extra": extra,
    }


@router.delete("/api/me/activity")
async def delete_my_activity(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete all usage_events for the current user (Art. 17 right to erasure).
    Returns the count of deleted rows."""
    n = (
        db.query(models.UsageEvent)
          .filter(models.UsageEvent.user_id == current_user.id)
          .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": int(n)}


@router.get("/api/library-stats")
async def library_stats(
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    # NOT instrumented as a usage_event: this endpoint is hit by App.vue's
    # footer refresh and the periodic /api/me heartbeat side-effect, not a
    # user-initiated page load. Recording it would flood usage_events with
    # idle noise. /api/browse is the real page-view signal.
    clearance = _clearance_of(current_user)

    accessible_books = (
        db.query(models.Book)
        .join(models.BookLocation, models.BookLocation.hash_id == models.Book.id)
        .filter(models.Book.clearance <= clearance)
    )

    total_books = accessible_books.with_entities(func.count(func.distinct(models.Book.id))).scalar() or 0

    language_rows = (
        accessible_books.with_entities(models.Book.languages).distinct().all()
    )
    languages: set[str] = set()
    for (langs,) in language_rows:
        if not langs:
            continue
        for piece in langs.split(","):
            piece = piece.strip().lower()
            if piece:
                languages.add(piece)
    total_languages = len(languages)

    now = datetime.now(timezone.utc)
    cutoff = now - ONLINE_WINDOW
    week_ago_iso = (now - timedelta(days=7)).isoformat()
    books_added_7d = (
        accessible_books.with_entities(func.count(func.distinct(models.Book.id)))
        .filter(models.Book.import_date > week_ago_iso)
        .scalar() or 0
    )

    def _ext_suffix_filter(exts: set[str]):
        return or_(*(
            func.lower(models.Book.original_filename).like(f"%.{e}")
            for e in exts
        ))

    total_audio = (
        accessible_books
        .filter(_ext_suffix_filter(_AUDIO_EXTS))
        .with_entities(func.count(func.distinct(models.Book.id)))
        .scalar() or 0
    )
    total_video = (
        accessible_books
        .filter(_ext_suffix_filter(_VIDEO_EXTS))
        .with_entities(func.count(func.distinct(models.Book.id)))
        .scalar() or 0
    )

    response: dict[str, object] = {
        "total_books": int(total_books),
        "total_audio": int(total_audio),
        "total_video": int(total_video),
        "total_languages": int(total_languages),
        "books_added_7d": int(books_added_7d),
        # Canonical format sets (same ones used for the counts above) so the
        # footer's audio/video links build their `ext:` search from a single
        # source of truth and can never drift from the displayed count.
        "audio_exts": sorted(_AUDIO_EXTS),
        "video_exts": sorted(_VIDEO_EXTS),
    }

    # User counts are only exposed to signed-in viewers.
    if current_user is not None:
        total_users = (
            db.query(func.count(models.User.id))
            .filter(models.User.is_active.is_(True))
            .scalar() or 0
        )
        with state._last_seen_lock:
            for uid, ts in list(state._last_seen.items()):
                if ts < cutoff:
                    del state._last_seen[uid]
            online_users = len(state._last_seen)
            live_uids = set(state._last_seen)
        # Distinct active sessions within the same window. The footer suffix
        # ("(N online in M sessions)") only renders when M > N, but the value
        # is always returned so the frontend doesn't have to special-case it.
        # Only count sessions of users who are actually live (in _last_seen):
        # after a restart, rehydrated sessions carry a login-time last_seen_at
        # that can fall inside the window even though their owner hasn't made a
        # request since the restart — counting those would inflate M above N and
        # render a contradictory footer that conflates distinct users as tabs.
        with state._active_sessions_lock:
            online_sessions = sum(
                1 for s in state._active_sessions.values()
                if s["user_id"] in live_uids
                and s.get("last_seen_at") and s["last_seen_at"] >= cutoff
            )
        response["total_users"] = int(total_users)
        response["online_users"] = int(online_users)
        response["online_sessions"] = int(online_sessions)

    return response


@router.put("/api/users/me/settings", response_model=schemas.UserResponse)
async def update_settings(
    settings: schemas.UserSettingsUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if settings.search_per_page is not None:
        current_user.search_per_page = max(10, min(settings.search_per_page, 200))
    if settings.real_name is not None:
        # Empty (or whitespace-only) clears the name; otherwise trim and cap length.
        cleaned = settings.real_name.strip()[:100]
        current_user.real_name = cleaned or None
    db.commit()
    db.refresh(current_user)
    return _user_response_dict(current_user)


def _delete_avatar_file(user_id: int, avatar_url: str | None) -> None:
    """Remove a user's avatar file from AVATAR_DIR. Guarded so only a file that
    lives inside AVATAR_DIR and is named with that user's own id is touched;
    a no-op for None / a foreign / an already-missing file."""
    if not avatar_url or not avatar_url.startswith("/api/avatars/"):
        return
    AVATAR_DIR = _m().AVATAR_DIR
    name = os.path.basename(avatar_url)
    path = os.path.join(AVATAR_DIR, name)
    if (name.startswith(f"{user_id}_")
            and os.path.abspath(path).startswith(os.path.abspath(AVATAR_DIR) + os.sep)
            and os.path.isfile(path)):
        try: os.remove(path)
        except OSError: pass


def _process_avatar(raw: bytes, dest_path: str) -> None:
    """Decode the uploaded bytes and write a normalized 512x512 JPEG to dest_path:
    bake EXIF orientation, drop alpha/metadata, and center-crop-to-cover so the
    result is always square regardless of the input. Raises on a non-image (the
    caller maps that to HTTP 400). The client already sends a square crop; this is
    the server-side guarantee (bounds size, strips metadata, normalizes format)."""
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im = ImageOps.fit(im, (512, 512), Image.LANCZOS)
        im.save(dest_path, "JPEG", quality=85)


@router.post("/api/users/me/avatar", response_model=schemas.UserResponse)
async def upload_avatar(file: UploadFile = File(...), current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Invalid image file")

    # Read into memory with a size cap (don't rely on nginx to bound the body).
    buf = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > _AVATAR_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Avatar too large")

    filename = f"{current_user.id}_{uuid.uuid4().hex}.jpg"
    filepath = os.path.join(_m().AVATAR_DIR, filename)
    # Pillow decode/resize is CPU-bound and blocking — run it off the event loop.
    try:
        await asyncio.to_thread(_process_avatar, bytes(buf), filepath)
    except Exception:
        try: os.remove(filepath)
        except OSError: pass
        raise HTTPException(status_code=400, detail="Invalid image file")

    # Remove the user's previous avatar so files don't accumulate (the new file
    # has a fresh uuid, so it is never the same path).
    _delete_avatar_file(current_user.id, current_user.avatar_url)

    current_user.avatar_url = f"/api/avatars/{filename}"
    db.commit()
    db.refresh(current_user)

    return _user_response_dict(current_user)


@router.delete("/api/users/me/avatar", response_model=schemas.UserResponse)
async def delete_avatar(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove the profile photo: delete the file from disk and clear avatar_url so
    the UI falls back to the user's initials. Idempotent — still 200 if none set."""
    _delete_avatar_file(current_user.id, current_user.avatar_url)
    current_user.avatar_url = None
    db.commit()
    db.refresh(current_user)
    return _user_response_dict(current_user)


@router.post("/api/register", response_model=schemas.Message)
async def register_user(request: Request, user: schemas.UserCreate, db: Session = Depends(get_db)):
    # Privacy Policy + ToS consent is required at submit. Recorded on the
    # registration_request row so it survives the gap until admin approval;
    # /api/set-password copies it forward to the users row.
    if not user.accepted_legal:
        _record_usage_event(
            request, "register", user=None,
            extra={"success": False, "email": user.email, "reason": "legal_not_accepted"},
        )
        raise HTTPException(status_code=400, detail="You must accept the Privacy Policy and Terms of Service to register.")

    # Check if user already exists
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        _record_usage_event(
            request, "register", user=None,
            extra={"success": False, "email": user.email, "reason": "email_exists"},
        )
        raise HTTPException(status_code=400, detail="Email already registered")

    # Check if a pending request already exists
    db_request = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.email == user.email).first()
    if db_request:
        _record_usage_event(
            request, "register", user=None,
            extra={"success": False, "email": user.email, "reason": "already_pending"},
        )
        raise HTTPException(status_code=400, detail="Registration request already pending")

    lang = user.language if user.language in ("en", "ru") else None
    db_req = models.RegistrationRequest(
        email=user.email,
        source=user.source,
        purpose=user.purpose,
        status="pending",
        accepted_legal_at=_now_iso(),
        language=lang,
        legal_version_accepted=LEGAL_VERSION,
    )
    db.add(db_req)
    db.commit()
    db.refresh(db_req)

    # Notify admin. Inline send (pre-existing) so we can record the outcome —
    # recipient, subject, whether SMTP accepted it — in the audit log, letting
    # the operator answer "did the request notification actually go out?" later.
    mail = email_utils.send_admin_notification(
        user_email=db_req.email,
        token=db_req.token,
        source=db_req.source,
        purpose=db_req.purpose
    )
    _audit(db, None, "registration.request",
           target_kind="registration_request", target_id=db_req.id,
           summary=f"Registration requested by {db_req.email}",
           details={"email": db_req.email, "source": db_req.source,
                    "purpose": db_req.purpose, "language": db_req.language,
                    "notify_email_to": mail["to"], "notify_email_subject": mail["subject"],
                    "notify_email_sent": mail["ok"], "notify_email_error": mail["error"]})
    db.commit()

    _record_usage_event(
        request, "register", user=None,
        extra={"success": True, "email": user.email},
    )
    return {"message": "Registration request queued for approval."}

@router.get("/api/admin/approve")
async def approve_user(token: str, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == token).first()
    if not db_req:
        return JSONResponse(status_code=404, content={"message": "Invalid or expired token."})

    if db_req.status == "approved":
        return JSONResponse(status_code=200, content={"message": "User already approved, waiting for password setup."})

    req_id = db_req.id
    email = db_req.email
    req_token = db_req.token
    lang = db_req.language or "en"
    db_req.status = "approved"
    db.commit()

    # Notify user to set password — in the locale they registered in. Inline so
    # the audit row can record whether the approval mail was actually sent: when
    # the user says "I never got it", the operator checks sent=true → spam folder.
    mail = email_utils.send_user_approval(email, req_token, language=lang)
    _audit(db, None, "registration.approve",
           target_kind="registration_request", target_id=req_id,
           summary=f"Registration approved for {email}",
           details={"email": email, "language": lang,
                    "approval_email_to": mail["to"], "approval_email_subject": mail["subject"],
                    "approval_email_sent": mail["ok"], "approval_email_error": mail["error"]})
    db.commit()

    return JSONResponse(status_code=200, content={"message": "User approved successfully. Email sent to set password."})

@router.post("/api/set-password", response_model=schemas.Message)
async def set_password(data: schemas.UserSetPassword, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == data.token).first()
    if not db_req or db_req.status != "approved":
        raise HTTPException(status_code=400, detail="Invalid or unapproved token.")

    # SetPasswordView now shows the same Privacy + ToS checkbox as registration,
    # so the user re-acknowledges the *current* docs at this final onboarding
    # step. Matches /api/register's wording at the analogous gate above.
    if not data.accepted_legal:
        raise HTTPException(
            status_code=400,
            detail="You must accept the Privacy Policy and Terms of Service to set your password.",
        )

    if len(data.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )

    # Create active user. The checkbox click is a fresh consent event against
    # the current LEGAL_VERSION — overriding whatever was recorded at registration
    # submit (which may be stale if the admin took days to approve, or NULL for
    # pre-0003 super-legacy requests).
    hashed_password = get_password_hash(data.password)
    real_name = (data.real_name or "").strip()[:100] or None
    new_user = models.User(
        email=db_req.email,
        hashed_password=hashed_password,
        real_name=real_name,
        is_active=True,
        accepted_legal_at=_now_iso(),
        legal_version_accepted=LEGAL_VERSION,
    )
    db.add(new_user)
    # Flush so new_user.id is assigned by autoincrement before _audit() reads it
    # for actor_user_id. Without this the row is still pending and id is None.
    db.flush()
    _audit(db, new_user, "legal.accept",
           target_kind="user", target_id=str(new_user.id),
           summary=f"Accepted legal version {LEGAL_VERSION}",
           details={"version": LEGAL_VERSION,
                    "previous_version": db_req.legal_version_accepted,
                    "during": "set_password"})
    db.delete(db_req)
    db.commit()

    return {"message": "Password set successfully. You can now log in."}

def _reset_rate_limited(ip: str) -> bool:
    """In-process per-IP sliding-window limiter for the unauthenticated reset
    endpoints. Returns True (→ 429) once this IP has made RESET_RL_MAX requests
    within the last RESET_RL_WINDOW_S seconds. IP-based, so a 429 leaks no
    email-existence signal. Backstop to the nginx `limit_req` on these routes (and
    the only cap in dev / manual-uvicorn runs). The bucket lives in `state` so
    conftest resets it per test; uvicorn is single-process, so one bucket is
    process-global and authoritative."""
    now = time.monotonic()
    cutoff = now - RESET_RL_WINDOW_S
    with state._reset_rl_lock:
        hits = [t for t in state._reset_rl.get(ip, ()) if t > cutoff]
        if len(hits) >= RESET_RL_MAX:
            state._reset_rl[ip] = hits          # keep the pruned window; don't record this hit
            return True
        hits.append(now)
        state._reset_rl[ip] = hits
        return False

@router.post("/api/forgot-password", response_model=schemas.Message)
async def forgot_password(data: schemas.ForgotPassword, request: Request):
    """Request a one-time password-reset link.

    Anti-enumeration: the response is the SAME generic message with the SAME
    status whether or not the email belongs to an account. The endpoint does only
    a per-IP rate-limit check and a constant-time hand-off to a background worker
    (_dispatch_reset_request) — the user lookup, token issue, DB commit, and SMTP
    send all happen OFF the request path (see background._process_reset_request),
    so neither a DB write+commit (which happens only for a real, active account)
    nor an SMTP round-trip can leak existence via response timing. A token is
    issued and an email sent ONLY for an existing, active user, at most once per
    RESET_RESEND_WINDOW."""
    ip = request.client.host if request.client else "unknown"
    if _reset_rate_limited(ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")

    lang = data.language if data.language in ("en", "ru") else "en"
    _m()._dispatch_reset_request(data.email, lang)
    return {"message": "If that email is registered, a password reset link has been sent."}

@router.post("/api/reset-password", response_model=schemas.Message)
async def reset_password(data: schemas.ResetPassword, request: Request, db: Session = Depends(get_db)):
    """Consume a reset token and set a new password.

    The token is single-use: the row's used_at is stamped and never accepted
    again. All three rejection reasons — unknown / already-used / expired —
    return the SAME generic 400 so the endpoint can't be used to probe token
    state. On success every one of the user's sessions is terminated (a
    credential change should evict any existing/compromised session); the user
    is not auto-logged-in — they sign in fresh with the new password."""
    ip = request.client.host if request.client else "unknown"
    if _reset_rate_limited(ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")

    if len(data.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )

    now_iso = _now_iso()
    token_hash = hashlib.sha256(data.token.encode()).hexdigest()
    row = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token_hash == token_hash
    ).first()
    if row is None or row.used_at is not None or row.expires_at < now_iso:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    user = db.query(models.User).filter(models.User.id == row.user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    user.hashed_password = get_password_hash(data.password)
    row.used_at = now_iso
    _audit(db, user, "password.reset_complete",
           target_kind="user", target_id=user.id,
           summary=f"Password reset completed for {user.email}",
           details={"email": user.email})

    # Terminate all of the user's sessions. _delete_user_sessions commits —
    # flushing the password change, the used_at stamp, and the audit row in the
    # same transaction — then we evict the in-memory entries (delete-then-evict,
    # the same order/locking discipline as admin deactivate). Reached through
    # `main` so tests can patch main._delete_user_sessions.
    _m()._delete_user_sessions(db, user.id)
    with state._active_sessions_lock:
        for j in [k for k, v in state._active_sessions.items() if v["user_id"] == user.id]:
            state._active_sessions.pop(j, None)

    return {"message": "Your password has been reset. You can now log in."}

@router.get("/api/legal/meta")
async def legal_meta():
    """Configuration the Privacy Policy / Terms of Service render-time
    interpolator reads. Public — no auth needed, since the documents
    themselves are public. Falls back to ADMIN_EMAIL for the contact when
    LEGAL_CONTACT_EMAIL is unset. Jurisdiction wording is no longer
    interpolated (it was unsound to substitute one English string into the
    Russian doc); it's hardcoded in each locale's markdown instead.

    `current_version` is the LEGAL_VERSION constant in database.py — the
    string each authenticated user's `legal_version_accepted` must match,
    otherwise the re-acceptance modal fires."""
    contact = os.environ.get("LEGAL_CONTACT_EMAIL") or os.environ.get("ADMIN_EMAIL") or ""
    return {
        "contact_email": contact,
        "current_version": LEGAL_VERSION,
    }


@router.post("/api/legal/accept", response_model=schemas.UserResponse)
async def legal_accept(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record that the calling user accepts the *current* LEGAL_VERSION. The
    act of POSTing is the acceptance — there is no body. The version comes
    from server-side LEGAL_VERSION (not from the client), so a stale client
    can't falsely "accept" an outdated version on the user's behalf.

    Writes one `legal.accept` row to admin_audit_log so the operator can
    answer "did user X actually accept version V, and when?" months later.
    The actor IS the user — the audit log isn't admin-only despite the
    table name; see _audit's docstring.

    Returns the same shape as /api/me so the SPA can refresh its local
    `currentUser` ref in one round-trip and unmount the re-acceptance modal."""
    previous_version = current_user.legal_version_accepted
    current_user.accepted_legal_at = _now_iso()
    current_user.legal_version_accepted = LEGAL_VERSION
    _audit(db, current_user, "legal.accept",
           target_kind="user", target_id=str(current_user.id),
           summary=f"Accepted legal version {LEGAL_VERSION}",
           details={"version": LEGAL_VERSION,
                    "previous_version": previous_version})
    db.commit()
    db.refresh(current_user)
    return _user_response_dict(current_user)


@router.get("/api/admin/reject")
async def reject_user(token: str, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == token).first()
    if not db_req:
        return JSONResponse(status_code=404, content={"message": "Invalid or expired token."})

    req_id = db_req.id   # snapshot before delete — target_id is a plain string, not a FK
    user_email = db_req.email
    lang = db_req.language or "en"
    db.delete(db_req)
    db.commit()

    # Notify user — in the locale they registered in. Inline so the audit row
    # can record whether the rejection mail was actually sent.
    mail = email_utils.send_user_rejection(user_email, language=lang)
    _audit(db, None, "registration.reject",
           target_kind="registration_request", target_id=req_id,
           summary=f"Registration rejected for {user_email}",
           details={"email": user_email, "language": lang,
                    "rejection_email_to": mail["to"], "rejection_email_subject": mail["subject"],
                    "rejection_email_sent": mail["ok"], "rejection_email_error": mail["error"]})
    db.commit()

    return JSONResponse(status_code=200, content={"message": "User rejected successfully."})
