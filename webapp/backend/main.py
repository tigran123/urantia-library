from fastapi import FastAPI, HTTPException, Request, Depends, Cookie, status, Response, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, not_, func, case, select, text as sa_text
from sqlalchemy.exc import IntegrityError
from fastapi.responses import JSONResponse, FileResponse, Response, StreamingResponse
import os
import re
import subprocess
import io
import hashlib
import zipfile
import base64
import shutil
import uuid
import secrets
import asyncio
import threading
import xml.etree.ElementTree as ET
from html import escape as _html_escape
from PIL import Image
import djvu.decode
from typing import List, Dict, Any, Optional
from pathlib import Path
import json
import logging
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import models
import schemas
from database import engine, get_db, SessionLocal, verify_schema_version, LEGAL_VERSION
from security import get_password_hash, verify_password, create_access_token, SECRET_KEY, ALGORITHM
import email_utils
import jwt
import geoip2.database

models.Base.metadata.create_all(bind=engine)
verify_schema_version(engine)


def _send_legal_blast_emails():
    """Background worker: mail every active user one bilingual notice that the
    Privacy Policy / Terms of Service have changed. Idempotency lives in
    `app_meta.legal_blast_version`; transient SMTP failures are accepted as
    lost blasts (consistent with how digest emails handle failure today). The
    in-app re-acceptance modal still fires on next login regardless of whether
    the email landed — the email is a courtesy, the modal is the binding ack."""
    db = SessionLocal()
    try:
        active = db.query(models.User).filter(models.User.is_active == True).all()
        sent = 0
        for u in active:
            try:
                email_utils.send_legal_update(u.email)
                sent += 1
            except Exception as e:
                logging.warning("legal blast: failed to mail %s: %s", u.email, e)
        logging.info("legal blast: mailed %d users for version %s", sent, LEGAL_VERSION)
    finally:
        db.close()


def _maybe_send_legal_blast():
    """At startup, fire the legal-update email blast iff LEGAL_VERSION has
    advanced since the last successful claim. Race-safe via a conditional
    UPDATE on app_meta — mirrors _maybe_send_feedback_digest and
    _maybe_send_moderation_digest.

    Self-seeds the throttle key with the CURRENT LEGAL_VERSION on first
    observation: a fresh install that booted from lib_schema.sql alone (no
    migration history) starts at the current version, so no spam blast
    fires for the seeded admin user. Migration 0004 seeds the same value
    on existing installs at upgrade time. The first real blast fires only
    on the NEXT LEGAL_VERSION bump."""
    with engine.begin() as conn:
        conn.execute(
            sa_text("INSERT OR IGNORE INTO app_meta(key, value) "
                    "VALUES ('legal_blast_version', :v)"),
            {"v": LEGAL_VERSION},
        )
        result = conn.execute(
            sa_text("UPDATE app_meta SET value = :new "
                    "WHERE key = 'legal_blast_version' AND value != :new"),
            {"new": LEGAL_VERSION},
        )
        won = result.rowcount > 0
    if not won:
        return
    threading.Thread(target=_send_legal_blast_emails, daemon=True).start()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Fires on uvicorn ASGI startup — NOT on bare `import main`. This is
    where one-time webapp startup side effects live (NOT module-level), so
    maintenance scripts (reimport_orphans.py, migrate.py) that import main
    don't trigger them.

    Each step is wrapped in try/except so a transient failure (e.g. the
    nightly online-backup briefly holding the SQLite write lock) doesn't
    block uvicorn from coming up and serving requests. The state these
    steps set up is either retried on the next restart or already covered
    by a lazy fallback inside the request path."""
    try:
        os.makedirs(RECOMMENDED_DIR, exist_ok=True)
    except OSError:
        # Logged but not fatal — `_create_recommendation` has its own lazy
        # `os.makedirs(..., exist_ok=True)`, so the next recommend action will
        # retry; routes that don't touch Recommended/ are unaffected.
        logging.exception("startup: failed to create RECOMMENDED_DIR")
    try:
        _maybe_send_legal_blast()
    except Exception:
        # Fire-and-forget telemetry, same posture as _record_usage_event. The
        # conditional UPDATE that claims the throttle key never committed on
        # an exception path, so the next restart will retry the claim.
        logging.exception("startup: legal blast trigger failed; will retry on next restart")
    try:
        # The restart we're recovering from is exactly what orphans staging dirs
        # (in-memory _STAGING was just wiped), so reap stale ones proactively.
        _purge_expired_staging()
    except Exception:
        logging.exception("startup: staging sweep failed; will retry on next upload/commit")
    yield

app = FastAPI(lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def custom_logging_middleware(request: Request, call_next):
    # Determine user email from cookie
    user_email = "Anonymous"
    access_token = request.cookies.get("access_token")
    if access_token:
        try:
            payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
            user_email = payload.get("sub", "Anonymous")
        except:
            pass

    # Client IP without port
    client_host = request.client.host if request.client else "unknown"

    response = await call_next(request)

    # Format current date/time
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Format and print the log
    url_with_query = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
    print(f"{now} {client_host} - {user_email} - \"{request.method} {url_with_query} HTTP/{request.scope.get('http_version', '1.1')}\" {response.status_code}")

    return response

@app.middleware("http")
async def strip_charset_for_websites(request: Request, call_next):
    response = await call_next(request)
    if "/Websites/" in request.url.path:
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type and "charset=utf-8" in content_type.lower():
            # Remove the forced utf-8 charset so the browser relies on the <meta> tag inside the file
            # By deleting it and recreating, we ensure Starlette's MutableHeaders handles it properly
            del response.headers["content-type"]
            response.headers["content-type"] = "text/html"
    return response

@app.middleware("http")
async def spa_cache_headers(request: Request, call_next):
    # The SPA shell (index.html) must never be cached: its <script>/<link>
    # tags reference content-hashed asset filenames, and a rebuild produces
    # new hashes. A stale cached index.html points at evicted hashes and the
    # browser 404s on CSS/JS, leaving the page unstyled until the user
    # Ctrl+R's. Hashed assets under /assets/ are immutable and safe to
    # cache aggressively.
    response = await call_next(request)
    if response.status_code != 200:
        return response
    path = request.url.path
    if path == "/" or path == "/index.html":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    elif path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")
DATA_DIR = os.path.join(BOOKS_DIR, ".data")
FEEDBACK_ATTACHMENT_DIR = os.environ.get(
    "FEEDBACK_ATTACHMENT_DIR", os.path.join(DATA_DIR, "feedback_attachments")
)
os.makedirs(FEEDBACK_ATTACHMENT_DIR, exist_ok=True)
# The Recommended/ pseudo-directory is managed exclusively by the
# recommend/unrecommend endpoints. It is browsable, but the generic
# file-management paths (upload, move, directory delete) must refuse it so it
# can't be populated, relocated, or destroyed out-of-band. It is NOT in
# _TOPDIR_SKIPLIST — we want it to appear in /api/browse — but it IS excluded
# from the upload tree picker (/api/admin/dirs).
RECOMMENDED_SUBDIR = "Recommended"
RECOMMENDED_DIR = os.path.join(BOOKS_DIR, RECOMMENDED_SUBDIR)
# Directory creation is deferred to `_lifespan` (so bare `import main` from
# maintenance scripts like reimport_orphans.py doesn't have a filesystem side
# effect) and to `_create_recommendation` (lazy first-use fallback).
_TOPDIR_SKIPLIST = {".claude", ".antigravitycli", ".vscode", ".data", "CLAUDE.md", "GEMINI.md", "urantia-library"}


def _is_recommended_path(rel: str) -> bool:
    """True if `rel` (a BOOKS_DIR-relative POSIX path) is the Recommended
    pseudo-directory or anything beneath it."""
    return rel == RECOMMENDED_SUBDIR or rel.startswith(RECOMMENDED_SUBDIR + "/")

# Offline IP→geo lookup for usage_events. The .mmdb is refreshed monthly by
# scripts/update_geoip.sh; the reader here is opened once at module load, so
# a fresh database only takes effect on the next service restart.
# A missing file is non-fatal: events still record with NULL geo.
GEOIP_DB_PATH = os.path.join(DATA_DIR, "geoip", "GeoLite2-City.mmdb")
try:
    _geoip_reader: Optional[geoip2.database.Reader] = (
        geoip2.database.Reader(GEOIP_DB_PATH) if os.path.exists(GEOIP_DB_PATH) else None
    )
    if _geoip_reader is None:
        print(f"WARN: GeoIP database not found at {GEOIP_DB_PATH}; "
              "usage events will record with NULL geo until "
              "scripts/update_geoip.sh seeds it.")
except Exception as e:
    print(f"WARN: failed to open GeoIP database at {GEOIP_DB_PATH}: {e}")
    _geoip_reader = None


def _geo_lookup(ip: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve an IP to (country_iso, city_name). Returns (None, None) for
    loopback, link-local, missing database, or any lookup error — never
    raises. Safe to call from the request path."""
    if not _geoip_reader or not ip:
        return (None, None)
    # Loopback / link-local short-circuit (no geo on dev or behind reverse
    # proxy that hasn't set XFF correctly).
    if ip.startswith("127.") or ip == "::1" or ip.startswith("fe80:"):
        return (None, None)
    try:
        r = _geoip_reader.city(ip)
        return (r.country.iso_code, r.city.name)
    except Exception:
        return (None, None)

_last_seen: dict[int, datetime] = {}
_last_seen_lock = threading.Lock()
ONLINE_WINDOW = timedelta(minutes=5)

# Active JWT sessions, keyed by the token's `jti` claim. A request whose jti
# is not here is treated as terminated (401), even if the JWT signature and
# `exp` are still valid. The map is in-process only — a backend restart
# wipes it and forces every cookie to re-login, which is the agreed
# semantics.
_active_sessions: dict[str, dict] = {}
_active_sessions_lock = threading.Lock()


def _purge_expired_sessions_locked() -> None:
    """Drop entries whose `expires_at` is in the past. Caller holds the lock."""
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _active_sessions.items() if v["expires_at"] <= now]
    for k in expired:
        _active_sessions.pop(k, None)


def _seed_admin_feedback_settings() -> None:
    """Idempotent seed of the singleton admin_feedback_settings row."""
    db = SessionLocal()
    try:
        existing = db.query(models.AdminFeedbackSettings).filter(
            models.AdminFeedbackSettings.id == 1
        ).first()
        if not existing:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            db.add(models.AdminFeedbackSettings(
                id=1, digest_interval_hours=6, min_batch_size=1,
                urgent_bypass=True, extra_recipients="", updated_at=now,
            ))
            db.commit()
    except Exception as e:
        print(f"failed to seed admin_feedback_settings: {e}")
        db.rollback()
    finally:
        db.close()


_seed_admin_feedback_settings()


def _resolve_vault_hash(symlink_path: str) -> str | None:
    """Return the BLAKE2b hash if `symlink_path` resolves into the CAS vault
    (i.e. /Books/.data/<hash>); None for unrelated symlinks like
    /Books/GEMINI.md -> CLAUDE.md."""
    try:
        target = os.path.realpath(symlink_path)
    except OSError:
        return None
    data_root = os.path.realpath(DATA_DIR)
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _current_jti(access_token: str | None) -> str | None:
    """Decode `jti` from an access_token cookie without validating against the
    session map. Used by admin handlers that need to know which session is
    the caller's own (to guard against self-termination), and by
    _record_usage_event() to cluster a single login's events."""
    if not access_token:
        return None
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload.get("jti")


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

    data_path = os.path.join(DATA_DIR, hash_id)
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
        full = os.path.join(BOOKS_DIR, sp)
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

async def get_current_user(access_token: str = Cookie(None), db: Session = Depends(get_db)):
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        jti: str = payload.get("jti")
        if email is None or jti is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    now = datetime.now(timezone.utc)
    with _active_sessions_lock:
        sess = _active_sessions.get(jti)
        if sess is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session terminated")
        sess["last_seen_at"] = now
    with _last_seen_lock:
        _last_seen[user.id] = now
    return user


def require_admin(current_user: models.User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return current_user


async def get_optional_user(
    access_token: str = Cookie(None), db: Session = Depends(get_db)
) -> models.User | None:
    """Like `get_current_user`, but returns `None` instead of raising 401 when
    there is no valid session. Used by guest-reachable endpoints: a `None` user
    is an anonymous visitor, treated as clearance 0 (see `_clearance_of` /
    `_is_admin`). Never raises."""
    if not access_token:
        return None
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        jti: str = payload.get("jti")
        if email is None or jti is None:
            return None
    except jwt.PyJWTError:
        return None
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not user.is_active:
        return None
    now = datetime.now(timezone.utc)
    with _active_sessions_lock:
        sess = _active_sessions.get(jti)
        if sess is None:
            return None
        sess["last_seen_at"] = now
    with _last_seen_lock:
        _last_seen[user.id] = now
    return user


def _clearance_of(user: models.User | None) -> int:
    """Clearance for a possibly-anonymous user; guests (`None`) are clearance 0."""
    return (user.clearance or 0) if user else 0


def _is_admin(user: models.User | None) -> bool:
    """True only for a signed-in admin; guests (`None`) are never admin."""
    return bool(user and user.is_admin)


def _audit(
    db: Session,
    actor: models.User,
    action: str,
    *,
    target_kind: str | None = None,
    target_id: str | int | None = None,
    summary: str,
    details: dict | None = None,
) -> None:
    """Append a row to admin_audit_log within the caller's open transaction.

    Why no commit here: the row rides along with whatever commit the calling
    endpoint is about to do for the underlying action. If that commit gets
    rolled back, the audit row vanishes with it — the desired invariant is
    "no audit entries for actions that didn't happen." The flip side is that
    the caller MUST eventually commit; an _audit() call without a subsequent
    db.commit() in the same request is a silent bug.

    `action` is a dotted controlled vocabulary; see admin_audit_log.action in
    lib_schema.sql for the canonical list. `target_id` accepts int (user id,
    comment id) or str (book hash) and is stored as text either way.
    """
    db.add(models.AdminAuditLog(
        created_at=_now_iso(),
        actor_user_id=actor.id,
        action=action,
        target_kind=target_kind,
        target_id=str(target_id) if target_id is not None else None,
        summary=summary,
        # `is not None` rather than truthiness — an intentional `details={}`
        # should still serialise (as the JSON "{}") instead of silently becoming NULL.
        details_json=json.dumps(details, separators=(",", ":")) if details is not None else None,
    ))


# batch_id -> (audit_row_id, last_touch_ts). In-memory, like _STAGING: a restart
# mid-batch just starts a fresh audit row for the remaining volumes (rare, and
# the batch's staging is gone too). Sequential per-batch commits (the client
# loops) mean no concurrent append to a row.
_AUDIT_BATCHES: dict[str, tuple[int, float]] = {}
_AUDIT_BATCHES_LOCK = threading.Lock()
_AUDIT_BATCH_TTL_S = 3600


def _audit_batch_upload(db: Session, actor: models.User, batch_id: str, book: dict, base_dir: str) -> None:
    """Fold one committed book into a single 'batch upload' audit row (one row per
    multi-book Commit-all run), creating it on the first commit and appending on
    the rest. Keeps the audit log compact for multi-volume sets while each book
    still rides its own commit transaction — so the row reflects exactly what was
    committed, even when a batch is only partially completed."""
    now = datetime.now(timezone.utc).timestamp()
    with _AUDIT_BATCHES_LOCK:
        for b in [b for b, (_i, ts) in _AUDIT_BATCHES.items() if now - ts > _AUDIT_BATCH_TTL_S]:
            _AUDIT_BATCHES.pop(b, None)
        entry = _AUDIT_BATCHES.get(batch_id)
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
    with _AUDIT_BATCHES_LOCK:
        _AUDIT_BATCHES[batch_id] = (row.id, now)


def _record_usage_event(
    request: Request,
    kind: str,
    *,
    user: models.User | None,
    hash_id: str | None = None,
    path: str | None = None,
    extra: dict | None = None,
) -> None:
    """Append a row to usage_events for the current request. Fire-and-forget:
    opens its own short-lived session, swallows any exception, never blocks
    the request path. Unlike _audit(), this does NOT ride the caller's
    transaction — telemetry must be independent of the action's success.

    `kind`     — 'page' | 'book_open' | 'search' | 'login' | 'register'
    `hash_id`  — book identifier when kind='book_open'; ignored otherwise
    `path`     — explicit path to record (defaults to request.url.path; pass
                 the ?path= value for /api/browse so the row reflects what
                 the user navigated to, not the API URL)
    `extra`    — per-kind details serialised into extra_json
    """
    # Honour the admin kill-switch (Admin → Usage → Settings). The cache is
    # rebuilt by _load_enabled_kinds() on save; an empty set silently drops
    # every recording while keeping existing rows in place.
    if kind not in _enabled_kinds:
        return
    try:
        ip = request.client.host if request.client else "unknown"
        ua = (request.headers.get("user-agent") or "")[:500] or None
        jti = _current_jti(request.cookies.get("access_token"))
        country, city = _geo_lookup(ip)
        raw_path = path if path is not None else request.url.path
        # Collapse repeated slashes so the log reads cleanly regardless of
        # uvicorn root-path quirks ("/" prefix → "//api/..." in request.url).
        norm_path = re.sub(r"/{2,}", "/", raw_path) if raw_path else raw_path
        db = SessionLocal()
        try:
            db.add(models.UsageEvent(
                ts=_now_iso(),
                user_id=user.id if user else None,
                session_jti=jti,
                ip=ip,
                user_agent=ua,
                geo_country=country,
                geo_city=city,
                kind=kind,
                path=norm_path,
                hash_id=hash_id,
                extra_json=json.dumps(extra, separators=(",", ":")) if extra is not None else None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        # Telemetry must never break a real request.
        pass


def _first_book_path(db: Session, hash_id: str) -> str | None:
    """Return one current symlink path for `hash_id`, or None if the book has
    no registered locations. Used by audit-write sites to snapshot a /item/<path>
    link target — the path freezes at audit time, so a later move/delete will
    surface to the admin as a broken link (intentional)."""
    row = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == hash_id
    ).first()
    return row[0] if row else None


def _primary_topic_path(db: Session, hash_id: str) -> str | None:
    """Return a current symlink path for `hash_id` that is NOT under
    Recommended/, or None. Used when recording usage events for
    recommend/unrecommend so the timeline's Path column shows the book's real
    topic location (e.g. Religions/Urantia/...) instead of the API URL or the
    synthetic Recommended/ symlink."""
    row = (
        db.query(models.BookLocation.symlink_path)
        .filter(
            models.BookLocation.hash_id == hash_id,
            models.BookLocation.symlink_path.notlike("Recommended/%"),
        )
        .first()
    )
    return row[0] if row else None


def _book_clearance(file_hash: str | None, db: Session) -> int:
    """Return the clearance required to read `file_hash`. 0 (public) if the
    hash is unknown or has no row in `books` — matches the design decision
    that ancillary, unregistered files are unrestricted."""
    if not file_hash:
        return 0
    book = db.query(models.Book).filter(models.Book.id == file_hash).first()
    return book.clearance if book else 0


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


def assert_can_read_path(symlink_fs_path: str, user: models.User | None, db: Session) -> None:
    """Resolve `symlink_fs_path` through the CAS vault, look up the book's
    clearance, and 403 if the user's clearance is below it. Admins bypass.
    Non-CAS files (symlinks pointing outside .data) are treated as public.
    A `None` user is an anonymous guest (clearance 0)."""
    if _is_admin(user):
        return
    file_hash = _resolve_vault_hash(symlink_fs_path)
    required = _book_clearance(file_hash, db)
    if required > _clearance_of(user):
        raise HTTPException(status_code=403, detail="Forbidden")


def _accessible_locations_query(db: Session, prefix: str, user: models.User | None):
    """Query for `book_locations.symlink_path` values under `prefix` that point
    to books readable by `user`. `prefix` should already end with '/' (or be ''
    for the library root). Relies on the PK index on symlink_path."""
    return (
        db.query(models.BookLocation.symlink_path)
        .join(models.Book, models.Book.id == models.BookLocation.hash_id)
        .filter(models.Book.clearance <= _clearance_of(user))
        .filter(models.BookLocation.symlink_path.like(f"{prefix}%"))
    )


@app.post("/api/login")
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
    with _active_sessions_lock:
        _purge_expired_sessions_locked()
        _active_sessions[jti] = {
            "user_id": user.id,
            "email": user.email,
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "created_at": now,
            "last_seen_at": now,
            "expires_at": expires_at,
        }
    response = JSONResponse(content={"message": "Login successful"})
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() != "false",
        samesite="lax",
        max_age=7*24*60*60
    )
    _record_usage_event(request, "login", user=user, extra={"success": True})
    return response

@app.post("/api/logout")
async def logout(access_token: str = Cookie(None)):
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
    # Decode the cookie inline rather than going through get_optional_user —
    # an already-terminated session must still be able to clear its own row,
    # and the dep would refuse it.
    if access_token:
        try:
            payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
            jti = payload.get("jti")
            email = payload.get("sub")
        except jwt.PyJWTError:
            jti = None
            email = None
        if jti:
            with _active_sessions_lock:
                _active_sessions.pop(jti, None)
        if email:
            db_local = SessionLocal()
            try:
                u = db_local.query(models.User).filter(models.User.email == email).first()
                if u is not None:
                    with _last_seen_lock:
                        _last_seen.pop(u.id, None)
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


@app.get("/api/me", response_model=schemas.UserResponse)
async def get_me(current_user: models.User = Depends(get_current_user)):
    return _user_response_dict(current_user)


# ==============================================================================
# My Activity — GDPR subject-rights surface for the signed-in user
# ==============================================================================
# Backs the #/account/activity page. GET returns the user's own usage_events,
# either paginated (default) or as a full JSON export (?format=json).
# DELETE wipes all events for this user — exercises Art. 17 ("right to be
# forgotten") for the data subject. Guest erasure is handled out-of-band via
# the contact-admin path documented in the Privacy Policy.

@app.get("/api/me/activity")
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


@app.delete("/api/me/activity")
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


@app.get("/api/library-stats")
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

    response: dict[str, int] = {
        "total_books": int(total_books),
        "total_audio": int(total_audio),
        "total_video": int(total_video),
        "total_languages": int(total_languages),
        "books_added_7d": int(books_added_7d),
    }

    # User counts are only exposed to signed-in viewers.
    if current_user is not None:
        total_users = (
            db.query(func.count(models.User.id))
            .filter(models.User.is_active.is_(True))
            .scalar() or 0
        )
        with _last_seen_lock:
            for uid, ts in list(_last_seen.items()):
                if ts < cutoff:
                    del _last_seen[uid]
            online_users = len(_last_seen)
        # Distinct active sessions within the same window. The footer suffix
        # ("(N online in M sessions)") only renders when M > N, but the value
        # is always returned so the frontend doesn't have to special-case it.
        with _active_sessions_lock:
            online_sessions = sum(
                1 for s in _active_sessions.values()
                if s.get("last_seen_at") and s["last_seen_at"] >= cutoff
            )
        response["total_users"] = int(total_users)
        response["online_users"] = int(online_users)
        response["online_sessions"] = int(online_sessions)

    return response


@app.put("/api/users/me/settings", response_model=schemas.UserResponse)
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

AVATAR_DIR = os.path.join(DATA_DIR, "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)
app.mount("/api/avatars", StaticFiles(directory=AVATAR_DIR), name="avatars")

@app.post("/api/users/me/avatar", response_model=schemas.UserResponse)
async def upload_avatar(file: UploadFile = File(...), current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Invalid image file")

    ext = file.filename.split(".")[-1]
    filename = f"{current_user.id}_{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(AVATAR_DIR, filename)

    # Stream with a size cap. Without it, any logged-in user could fill the
    # disk now that nginx allows large request bodies through.
    bytes_written = 0
    try:
        with open(filepath, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _AVATAR_MAX_BYTES:
                    buffer.close()
                    os.remove(filepath)
                    raise HTTPException(status_code=400, detail="Avatar too large")
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        try: os.remove(filepath)
        except OSError: pass
        raise HTTPException(status_code=500, detail=f"Avatar upload failed: {e}")

    avatar_url = f"/api/avatars/{filename}"
    current_user.avatar_url = avatar_url
    db.commit()
    db.refresh(current_user)

    return _user_response_dict(current_user)

@app.get("/api/browse")
async def browse(request: Request, path: str = "", current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    target_dir = os.path.join(BOOKS_DIR, path)
    if not os.path.abspath(target_dir).startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    # For non-admins, hide subdirectories whose subtree contains no readable book
    # (and 403 on direct access to such a directory) so the topic structure of
    # the library isn't leaked via directory names.
    accessible_subdirs: set[str] = set()
    if not _is_admin(current_user):
        prefix = f"{path.rstrip('/')}/" if path else ""
        rows = _accessible_locations_query(db, prefix, current_user).all()
        if path and not rows:
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
        is_dir = os.path.isdir(entry_path)
        if is_dir and not _is_admin(current_user) and entry not in accessible_subdirs:
            continue

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

        if os.path.islink(entry_path):
            file_hash = _resolve_vault_hash(entry_path)
            if file_hash:
                item_data["hash_id"] = file_hash
                cover_fs_path = os.path.join(BOOKS_DIR, ".data", "covers", f"{file_hash}.jpg")
                if os.path.exists(cover_fs_path):
                    item_data["cover_url"] = f"/api/covers/{file_hash}"
                book = db.query(models.Book).filter(models.Book.id == file_hash).first()
                if book:
                    if not _is_admin(current_user) and (book.clearance or 0) > _clearance_of(current_user):
                        continue
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

    _record_usage_event(request, "page", user=current_user, path=path or "/")
    return {"path": path, "items": items}

# --- Intelligent search -----------------------------------------------------
#
# A query is a sequence of tokens. Every positive token must match somewhere
# (AND across tokens); each token is matched across all searchable columns
# (OR across columns). A token may be quoted (contiguous phrase), prefixed with
# `-` (exclusion), or scoped to one column (`author:harnum`). The structural
# keys `path:`/`ext:`/`needs_review:` keep their old filter semantics.

# Columns searched when a token has no explicit field scope.
_SEARCH_ALL_COLUMNS = [
    models.Book.title, models.Book.author, models.Book.description,
    models.Book.publisher, models.Book.series, models.Book.tags,
    models.Book.original_filename,
]
# Field scopes the user may name explicitly (`tag` is an alias for `tags`).
_SEARCH_FIELD_COLUMNS = {
    "title": models.Book.title,
    "author": models.Book.author,
    "description": models.Book.description,
    "publisher": models.Book.publisher,
    "series": models.Book.series,
    "tags": models.Book.tags,
}
_SEARCH_FIELD_ALIASES = {"tag": "tags"}
_STRUCTURAL_KEYS = {"path", "ext", "needs_review", "clearance"}
# Relevance weight of a scoped-field match (used by _relevance_score).
_SEARCH_FIELD_WEIGHT = {
    "title": 4, "author": 3, "publisher": 2, "series": 2, "tags": 2,
    "description": 1,
}

_SEARCH_TOKEN_RE = re.compile(r'''
    (?P<neg>-)?                          # optional exclusion marker
    (?:(?P<field>[A-Za-z_]+):)?          # optional field scope
    (?:
        "(?P<dq>[^"]*)"                  # double-quoted phrase
      | '(?P<sq>[^']*)'                  # single-quoted phrase
      | (?P<word>[^\s"']+)               # bare word
    )
''', re.VERBOSE)


def parse_search_query(q: str):
    """Tokenize a raw query string.

    Returns ``(terms, filters)`` where ``filters`` holds the structural
    path/ext/needs_review filters and ``terms`` is a list of dicts:
    ``{text, field, negate, is_phrase}`` (text is lowercased)."""
    filters = {"path": None, "ext": [], "needs_review": None, "clearance": None}
    terms = []

    for m in _SEARCH_TOKEN_RE.finditer(q or ""):
        negate = m.group("neg") is not None
        field = (m.group("field") or "").lower()
        quoted = m.group("dq") is not None or m.group("sq") is not None
        value = m.group("dq")
        if value is None:
            value = m.group("sq")
        if value is None:
            value = m.group("word") or ""

        if field in _STRUCTURAL_KEYS:
            val = value.strip().lower()
            if not val:
                continue
            if field == "path":
                filters["path"] = val
            elif field == "ext":
                # Multiple ext: tokens accumulate and are OR'd at query time
                # (ext:mp4 ext:mp3 → match either), deduped.
                e = val if val.startswith(".") else "." + val
                if e not in filters["ext"]:
                    filters["ext"].append(e)
            elif field == "needs_review":
                if val in ("1", "true", "yes"):
                    filters["needs_review"] = True
                elif val in ("0", "false", "no"):
                    filters["needs_review"] = False
            elif field == "clearance":
                m = re.fullmatch(r"(\d+)-(\d+)", val)
                if m:
                    n1, n2 = int(m.group(1)), int(m.group(2))
                    if n1 > n2:
                        n1, n2 = n2, n1
                    filters["clearance"] = ("between", n1, n2)
                else:
                    op = "eq"
                    num = val
                    if val.endswith("+"):
                        op, num = "gt", val[:-1].strip()
                    elif val.endswith("-"):
                        op, num = "lt", val[:-1].strip()
                    try:
                        n = int(num)
                    except ValueError:
                        continue
                    if n < 0:
                        continue
                    filters["clearance"] = (op, n, None)
            continue

        scope = _SEARCH_FIELD_ALIASES.get(field, field)
        if scope not in _SEARCH_FIELD_COLUMNS:
            # Unknown `field:` prefix — treat the whole thing as literal text.
            scope = None
            if field:
                value = m.group("field") + ":" + value

        text = value.strip().lower()
        if not text:
            continue
        terms.append({
            "text": text,
            "field": scope,
            "negate": negate,
            "is_phrase": quoted,
        })

    return terms, filters


def _escape_like(text: str) -> str:
    """Escape SQL LIKE metacharacters in user text (used with ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _wildcard_escape(text: str) -> str:
    """Escape LIKE metacharacters, then map the user wildcards `*`/`?` to the
    SQL wildcards `%`/`_`."""
    return _escape_like(text).replace("*", "%").replace("?", "_")


def _like_pattern(text: str, is_phrase: bool) -> str:
    """Build a `%…%` LIKE pattern. SQL metacharacters in user text are escaped
    (ESCAPE '\\'); for non-phrase tokens the user wildcards `*`/`?` are then
    translated to SQL `%`/`_`."""
    esc = _escape_like(text) if is_phrase else _wildcard_escape(text)
    return f"%{esc}%"


def _col_expr(col):
    """Case-folded, NULL-safe column expression for LIKE matching."""
    return func.lower(func.coalesce(col, ""))


def _term_condition(term: dict):
    """SQL predicate for a single (positive) search term."""
    pattern = _like_pattern(term["text"], term["is_phrase"])
    if term["field"]:
        col = _SEARCH_FIELD_COLUMNS[term["field"]]
        return _col_expr(col).like(pattern, escape="\\")
    return or_(*[
        _col_expr(c).like(pattern, escape="\\") for c in _SEARCH_ALL_COLUMNS
    ])


def _relevance_score(terms: list):
    """Build an ORDER-BY relevance expression, or None if there is nothing to
    rank (no positive terms)."""
    positive = [t for t in terms if not t["negate"]]
    if not positive:
        return None

    score = None
    for t in positive:
        pattern = _like_pattern(t["text"], t["is_phrase"])
        if t["field"]:
            col = _SEARCH_FIELD_COLUMNS[t["field"]]
            term_score = case(
                (_col_expr(col).like(pattern, escape="\\"),
                 _SEARCH_FIELD_WEIGHT.get(t["field"], 1)),
                else_=0,
            )
        else:
            term_score = case(
                (_col_expr(models.Book.title).like(pattern, escape="\\"), 4),
                (_col_expr(models.Book.author).like(pattern, escape="\\"), 3),
                (or_(
                    _col_expr(models.Book.publisher).like(pattern, escape="\\"),
                    _col_expr(models.Book.series).like(pattern, escape="\\"),
                    _col_expr(models.Book.tags).like(pattern, escape="\\"),
                ), 2),
                (_col_expr(models.Book.description).like(pattern, escape="\\"), 1),
                else_=0,
            )
        score = term_score if score is None else score + term_score

    # Bonus when the whole plain-text query appears contiguously in the title.
    full = " ".join(
        t["text"] for t in positive if t["field"] is None and not t["is_phrase"]
    )
    if full:
        bonus_pat = _like_pattern(full, True)
        score = score + case(
            (_col_expr(models.Book.title).like(bonus_pat, escape="\\"), 50),
            else_=0,
        )
    return score


def _build_search_query(q: str, current_user: models.User | None, db: Session):
    """Shared query builder for /api/search and /api/search/hash_ids.
    Returns ``(query, terms)`` — the joined Book+BookLocation query with all
    filters applied, plus the parsed terms (for relevance ranking)."""
    terms, filters = parse_search_query(q)

    query = db.query(models.Book, models.BookLocation).join(
        models.BookLocation, models.Book.id == models.BookLocation.hash_id
    )

    if not _is_admin(current_user):
        query = query.filter(models.Book.clearance <= _clearance_of(current_user))

    conds = []
    for t in terms:
        cond = _term_condition(t)
        conds.append(not_(cond) if t["negate"] else cond)
    if conds:
        query = query.filter(and_(*conds))

    if filters["path"]:
        # `*`/`?` in the path act as wildcards (e.g. path:Law/*2024).
        pat = _wildcard_escape(filters["path"])
        query = query.filter(
            func.lower(models.BookLocation.symlink_path).like(f"{pat}%", escape="\\")
        )

    if filters["ext"]:
        # parse_search_query normalizes each ext to start with a dot; `*`/`?`
        # are wildcards, so `ext:*` matches every file with an extension.
        # Multiple ext: values are OR'd (ext:mp4 ext:mp3 → either).
        query = query.filter(or_(*[
            func.lower(models.BookLocation.symlink_path).like(f"%{_wildcard_escape(e)}", escape="\\")
            for e in filters["ext"]
        ]))

    if filters["needs_review"] is not None and _is_admin(current_user):
        query = query.filter(models.Book.needs_review == filters["needs_review"])

    if filters["clearance"] is not None:
        op, n1, n2 = filters["clearance"]
        if op == "eq":
            query = query.filter(models.Book.clearance == n1)
        elif op == "gt":
            query = query.filter(models.Book.clearance > n1)
        elif op == "lt":
            query = query.filter(models.Book.clearance < n1)
        elif op == "between":
            query = query.filter(models.Book.clearance.between(n1, n2))

    return query, terms


@app.get("/api/search")
async def search(
    request: Request,
    q: str = "",
    page: int = 1,
    per_page: int = 50,
    sort: str = Query("relevance", pattern="^(relevance|size|directory)$"),
    direction: str = Query("desc", alias="dir", pattern="^(asc|desc)$"),
    cols: int = 1,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))
    cols = max(1, min(cols, 50))

    if not q:
        return {"matches": [], "page": page, "per_page": per_page, "total": 0, "total_pages": 0,
                "sort": sort, "dir": direction}

    query, terms = _build_search_query(q, current_user, db)

    # Build ORDER BY. `direction` only meaningfully affects size and directory;
    # relevance is always score-desc with a stable tiebreak.
    asc = direction == "asc"
    order_cols: list = []
    if sort == "size":
        size_col = models.Book.size
        order_cols.append((size_col.asc() if asc else size_col.desc()).nulls_last())
        order_cols += [models.Book.title, models.Book.id, models.BookLocation.symlink_path]
    elif sort == "directory":
        # Lexicographic ordering on the full path puts same-parent siblings
        # adjacent (their shared prefix sorts together), and within a group
        # falls back to filename order — exactly what we want for multi-volume
        # sets. No SQL dirname() needed.
        path_col = models.BookLocation.symlink_path
        order_cols.append(path_col.asc() if asc else path_col.desc())
        order_cols.append(models.Book.id)
    else:
        avg_rating = (
            select(func.avg(models.BookRating.rating))
            .where(models.BookRating.hash_id == models.Book.id)
            .correlate(models.Book)
            .scalar_subquery()
        )
        score = _relevance_score(terms)
        if score is not None:
            order_cols.append(score.desc())
        order_cols += [
            avg_rating.desc().nulls_last(),
            models.Book.title, models.Book.id, models.BookLocation.symlink_path,
        ]

    # Page-boundary strategy. In directory mode + grid view (cols >= 2), pages
    # are computed so that the last directory on each page either ends naturally
    # at a row boundary or — when it would otherwise have a partial last row
    # AND the spillover items belong to the same directory — gets extended by
    # up to (cols-1) items to fill that row. This eliminates the visual ugliness
    # where, e.g., a directory of 10 items at 13-wide grid splits 8/2 across two
    # pages instead of 10/0. We do the walk in Python over a path-only
    # projection of the sorted result set; for any realistic library this is
    # microseconds and avoids a much more invasive offset-based pagination.
    if sort == "directory" and cols >= 2:
        paths: list[str] = [
            row[0] for row in
            query.with_entities(models.BookLocation.symlink_path)
                 .order_by(*order_cols)
                 .all()
        ]
        total = len(paths)
        dirs = [os.path.dirname(p) for p in paths]
        boundaries: list[int] = [0]
        i = 0
        while i < total:
            j = min(i + per_page, total)
            if j < total:
                last_dir = dirs[j - 1]
                k = j - 1
                while k > i and dirs[k - 1] == last_dir:
                    k -= 1
                dir_in_page = j - k
                extra = 0
                while j + extra < total and dirs[j + extra] == last_dir:
                    extra += 1
                if extra > 0 and dir_in_page % cols != 0:
                    take = min(cols - (dir_in_page % cols), extra)
                    j += take
            boundaries.append(j)
            i = j
        total_pages = max(1, len(boundaries) - 1)
        if page > total_pages:
            results = []
        else:
            start_idx = boundaries[page - 1]
            end_idx = boundaries[page]
            results = (
                query.order_by(*order_cols)
                .offset(start_idx)
                .limit(end_idx - start_idx)
                .all()
            )
    else:
        total = query.order_by(None).count()
        total_pages = (total + per_page - 1) // per_page
        results = (
            query.order_by(*order_cols)
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

    matches = []
    for book, loc in results:
        sym_path = loc.symlink_path
        cover_fs_path = os.path.join(BOOKS_DIR, ".data", "covers", f"{book.id}.jpg")
        cover_url = f"/api/covers/{book.id}" if os.path.exists(cover_fs_path) else None
        # books.size is populated at upload time and by backfill_sizes.py; a NULL
        # here means a row that hasn't been backfilled yet (rare). Falling back
        # to os.path.getsize would re-introduce the per-request stat we just
        # eliminated, so just return None and let the frontend hide the size.
        matches.append({
            "name": os.path.basename(sym_path),
            "is_dir": False,
            "path": sym_path,
            "parent_dir": os.path.dirname(sym_path),
            "cover_url": cover_url,
            "hash_id": book.id,
            "title": book.title,
            "author": book.author,
            "description": book.description,
            "clearance": int(book.clearance or 0),
            "size": book.size,
        })

    stats = _rating_stats(db, [m["hash_id"] for m in matches])
    for m in matches:
        s = stats.get(m["hash_id"])
        m["avg_rating"] = s["avg_rating"] if s else None
        m["rating_count"] = s["rating_count"] if s else 0

    _attach_recommendations(matches, db)

    # Record only non-empty searches; an empty `q` was short-circuited above.
    _record_usage_event(
        request, "search",
        user=current_user,
        extra={"q": q, "page": page, "total": int(total)},
    )

    return {
        "matches": matches,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "sort": sort,
        "dir": direction,
    }


@app.get("/api/search/hash_ids")
async def search_hash_ids(
    q: str = "",
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all distinct hash_ids matching the search query, across all pages.
    Used by the admin bulk-verify 'Select All' action."""
    if not q:
        return {"hash_ids": [], "total": 0}
    query, _ = _build_search_query(q, current_user, db)
    rows = query.with_entities(models.Book.id).distinct().all()
    ids = [r[0] for r in rows]
    return {"hash_ids": ids, "total": len(ids)}


@app.post("/api/register", response_model=schemas.Message)
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

    # Notify admin
    email_utils.send_admin_notification(
        user_email=db_req.email,
        token=db_req.token,
        source=db_req.source,
        purpose=db_req.purpose
    )

    _record_usage_event(
        request, "register", user=None,
        extra={"success": True, "email": user.email},
    )
    return {"message": "Registration request queued for approval."}

@app.get("/api/admin/approve")
async def approve_user(token: str, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == token).first()
    if not db_req:
        return JSONResponse(status_code=404, content={"message": "Invalid or expired token."})

    if db_req.status == "approved":
        return JSONResponse(status_code=200, content={"message": "User already approved, waiting for password setup."})

    db_req.status = "approved"
    db.commit()

    # Notify user to set password — in the locale they registered in.
    email_utils.send_user_approval(db_req.email, db_req.token, language=db_req.language or "en")

    return JSONResponse(status_code=200, content={"message": "User approved successfully. Email sent to set password."})

@app.post("/api/set-password", response_model=schemas.Message)
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

@app.get("/api/legal/meta")
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


@app.post("/api/legal/accept", response_model=schemas.UserResponse)
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


@app.get("/api/admin/reject")
async def reject_user(token: str, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == token).first()
    if not db_req:
        return JSONResponse(status_code=404, content={"message": "Invalid or expired token."})

    user_email = db_req.email
    lang = db_req.language or "en"
    db.delete(db_req)
    db.commit()

    # Notify user — in the locale they registered in.
    email_utils.send_user_rejection(user_email, language=lang)

    return JSONResponse(status_code=200, content={"message": "User rejected successfully."})


# ---------------- Admin: clearance management ----------------

@app.get("/api/admin/users", response_model=List[schemas.AdminUserSummary])
async def admin_list_users(
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return db.query(models.User).order_by(models.User.email).all()


@app.get("/api/admin/sessions", response_model=List[schemas.AdminSessionSummary])
async def admin_list_sessions(
    access_token: str = Cookie(None),
    _admin: models.User = Depends(require_admin),
):
    self_jti = _current_jti(access_token)
    with _active_sessions_lock:
        _purge_expired_sessions_locked()
        rows = [
            {
                "jti": jti,
                "user_id": s["user_id"],
                "email": s["email"],
                "ip_address": s["ip_address"],
                "user_agent": s["user_agent"],
                "created_at": s["created_at"].isoformat(),
                "last_seen_at": s["last_seen_at"].isoformat(),
                "is_self": jti == self_jti,
            }
            for jti, s in _active_sessions.items()
        ]
    rows.sort(key=lambda r: r["last_seen_at"], reverse=True)
    return rows


@app.delete("/api/admin/sessions/{jti}")
async def admin_terminate_session(
    jti: str,
    access_token: str = Cookie(None),
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if jti == _current_jti(access_token):
        raise HTTPException(status_code=400, detail="Refusing to terminate own session")
    # Peek at the session row first so we have something to audit; only after
    # the audit row has actually committed do we evict from memory. If commit
    # fails, the target still has their session and the admin sees a 5xx — no
    # torn-write window where the user is logged out but the log says nothing.
    with _active_sessions_lock:
        target = _active_sessions.get(jti)
    if target is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _audit(db, admin, "user.session_terminate",
           target_kind="user", target_id=target["user_id"],
           summary=f'Terminated session of {target["email"]}',
           details={"email": target["email"], "jti": jti, "ip_address": target.get("ip_address")})
    db.commit()
    with _active_sessions_lock:
        _active_sessions.pop(jti, None)   # idempotent — another request might have evicted concurrently
    return {"jti": jti, "terminated": True}


@app.put("/api/admin/users/{user_id}/clearance", response_model=schemas.AdminUserSummary)
async def admin_set_user_clearance(
    user_id: int,
    payload: schemas.UserClearanceUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    diff: dict[str, list] = {}
    if payload.clearance is not None:
        if payload.clearance < 0:
            raise HTTPException(status_code=400, detail="Clearance must be non-negative")
        if user.clearance != payload.clearance:
            diff["clearance"] = [user.clearance, payload.clearance]
        user.clearance = payload.clearance
    if payload.is_admin is not None:
        # Guard: don't let an admin demote themselves into having zero admins.
        if user.id == admin.id and payload.is_admin is False:
            raise HTTPException(status_code=400, detail="Refusing to demote the current admin")
        if bool(user.is_admin) != bool(payload.is_admin):
            diff["is_admin"] = [bool(user.is_admin), bool(payload.is_admin)]
        user.is_admin = payload.is_admin
    if payload.is_active is not None:
        # Guard: an admin must not lock themselves out of the system.
        if user.id == admin.id and payload.is_active is False:
            raise HTTPException(status_code=400, detail="Refusing to deactivate the current admin")
        if bool(user.is_active) != bool(payload.is_active):
            diff["is_active"] = [bool(user.is_active), bool(payload.is_active)]
        user.is_active = payload.is_active
    if diff:
        _audit(db, admin, "user.clearance",
               target_kind="user", target_id=user.id,
               summary=f'Updated {user.email}: {", ".join(diff.keys())}',
               details={"email": user.email, "changed": diff})
    db.commit()
    db.refresh(user)
    return user


@app.put("/api/admin/books/{hash_id}/clearance")
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


@app.post("/api/admin/books/clearance")
async def admin_bulk_set_book_clearance(
    payload: schemas.BulkBookClearanceUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.clearance < 0:
        raise HTTPException(status_code=400, detail="Clearance must be non-negative")
    if not payload.hash_ids:
        return {"updated": 0, "clearance": payload.clearance}
    # SQLite's UPDATE returns rows-matched, not rows-changed, so a no-op
    # bulk (every book already at the target clearance) would otherwise emit
    # an audit row claiming a mass change. Restrict the UPDATE to the hashes
    # whose value actually differs.
    changing_ids = [
        h for (h,) in db.query(models.Book.id)
            .filter(
                models.Book.id.in_(payload.hash_ids),
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
    sanitized version of the book title + the format extension carried by
    original_filename; fall back to a sanitized original_filename when the
    title is empty, and to the truncated hash when even that yields nothing
    after sanitization. Every return value goes through `_sanitize_for_fs`
    so `os.path.join(RECOMMENDED_DIR, ...)` cannot escape RECOMMENDED_DIR
    via `../` segments in user-controlled metadata."""
    title = _sanitize_for_fs(book.title or "")
    ext = ""
    if book.original_filename and "." in book.original_filename:
        ext = "." + _sanitize_for_fs(book.original_filename.rsplit(".", 1)[-1])
        if ext == ".":
            ext = ""
    if title:
        return f"{title}{ext}" if ext else title
    fallback = _sanitize_for_fs(book.original_filename or "")
    return fallback or book.id[:12]


def _find_free_recommended_name(base: str) -> str:
    """Append `-2`, `-3`, … to the stem until the name is free under
    RECOMMENDED_DIR. Uses os.path.lexists so broken symlinks still count as
    collisions."""
    if not os.path.lexists(os.path.join(RECOMMENDED_DIR, base)):
        return base
    if "." in base:
        stem, _, ext = base.rpartition(".")
        ext = "." + ext
    else:
        stem, ext = base, ""
    i = 2
    while True:
        candidate = f"{stem}-{i}{ext}"
        if not os.path.lexists(os.path.join(RECOMMENDED_DIR, candidate)):
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
    os.makedirs(RECOMMENDED_DIR, exist_ok=True)
    name = _find_free_recommended_name(base)
    rel = f"Recommended/{name}"
    dst_abs = os.path.join(RECOMMENDED_DIR, name)
    # Defense-in-depth: `_recommended_basename` already sanitises both branches,
    # but a future regression that lets `..` segments through must not be able
    # to land the symlink outside RECOMMENDED_DIR. Mirrors the abspath guard
    # used throughout main.py for BOOKS_DIR.
    if not os.path.abspath(dst_abs).startswith(os.path.abspath(RECOMMENDED_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Cannot derive a filename for recommendation")
    vault_path = os.path.join(DATA_DIR, book.id)
    target = os.path.relpath(vault_path, RECOMMENDED_DIR)
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
        abs_path = os.path.join(BOOKS_DIR, loc.symlink_path)
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


@app.post("/api/admin/books/{hash_id}/recommend", response_model=schemas.RecommendationResponse)
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
                os.unlink(os.path.join(BOOKS_DIR, new_symlink))
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


@app.delete("/api/admin/books/{hash_id}/recommend", response_model=schemas.UnrecommendResponse)
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


@app.post("/api/admin/books/recommend/bulk", response_model=schemas.BulkRecommendResponse)
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
    if not payload.hash_ids:
        return out
    # Dedup while preserving order — a client posting ["abc", "abc"] would
    # otherwise pass the `already` pre-check twice and produce duplicate
    # symlinks (Recommended/X.pdf, Recommended/X-2.pdf) plus a PK conflict at
    # commit. Frontend currently dedups, but the API contract makes no such
    # guarantee.
    hash_ids = list(dict.fromkeys(payload.hash_ids))
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
                os.unlink(os.path.join(BOOKS_DIR, sym))
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


@app.post("/api/admin/books/unrecommend/bulk", response_model=schemas.BulkUnrecommendResponse)
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
    if not payload.hash_ids:
        return out
    # Dedup while preserving order — see admin_recommend_bulk for why.
    hash_ids = list(dict.fromkeys(payload.hash_ids))
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


def _cover_url_for(hash_id: str) -> Optional[str]:
    """Return a cache-busted URL for the book's cover if the JPEG exists in
    the vault, else None."""
    cover_path = os.path.join(DATA_DIR, "covers", f"{hash_id}.jpg")
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


@app.get("/api/admin/books/{hash_id}", response_model=schemas.AdminBookDetail)
async def admin_get_book(
    hash_id: str,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return _book_to_admin_detail(book, db)


@app.put("/api/admin/books/{hash_id}", response_model=schemas.AdminBookDetail)
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


@app.delete("/api/admin/books/{hash_id}")
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

    # Filesystem cleanup. Tolerate missing files — best-effort.
    errors = []
    books_root = os.path.abspath(BOOKS_DIR)
    for sp in locations:
        full = os.path.abspath(os.path.join(BOOKS_DIR, sp))
        # Refuse to touch anything that escapes BOOKS_DIR or that isn't a symlink.
        if not full.startswith(books_root + os.sep):
            errors.append(f"refused traversal: {sp}")
            continue
        if os.path.islink(full):
            try:
                os.remove(full)
            except OSError as e:
                errors.append(f"symlink {sp}: {e}")

    vault_file = os.path.join(BOOKS_DIR, ".data", hash_id)
    if os.path.exists(vault_file):
        try:
            os.remove(vault_file)
        except OSError as e:
            errors.append(f"vault: {e}")
    cover_file = os.path.join(BOOKS_DIR, ".data", "covers", f"{hash_id}.jpg")
    if os.path.exists(cover_file):
        try:
            os.remove(cover_file)
        except OSError as e:
            errors.append(f"cover: {e}")

    # Foreign keys in SQLite aren't enforced unless PRAGMA foreign_keys=ON,
    # which we don't set — explicitly clear referencing rows so we don't leave
    # orphans in favorites / reading_progress / book_locations.
    db.query(models.BookLocation).filter(models.BookLocation.hash_id == hash_id).delete()
    db.query(models.Favorite).filter(models.Favorite.hash_id == hash_id).delete()
    db.query(models.ReadingProgress).filter(models.ReadingProgress.hash_id == hash_id).delete()
    db.query(models.BookRating).filter(models.BookRating.hash_id == hash_id).delete()
    db.query(models.BookComment).filter(models.BookComment.hash_id == hash_id).delete()
    db.query(models.BookRecommendation).filter(models.BookRecommendation.hash_id == hash_id).delete()
    db.query(models.PlaylistItem).filter(models.PlaylistItem.book_hash_id == hash_id).delete()
    db.delete(book)
    _audit(db, admin, "book.delete",
           target_kind="book", target_id=hash_id,
           summary=f'Deleted "{deleted_title}"',
           details={"title": deleted_title,
                    "path": locations[0] if locations else None,
                    "locations": locations})
    db.commit()
    return {"deleted": hash_id, "locations": locations, "errors": errors}


# ---------- Admin: move book(s) between locations ----------


def _rel_under_books(p: str) -> str:
    """Normalise an input path to a relative POSIX path under BOOKS_DIR.
    Strips leading/trailing slashes, rejects empty + traversal. Mirrors the
    inline guard used by the commit endpoint at main.py:1496-1498."""
    rel = (p or "").strip().lstrip("/").rstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="Empty path")
    abs_p = os.path.abspath(os.path.join(BOOKS_DIR, rel))
    books_abs = os.path.abspath(BOOKS_DIR)
    if not (abs_p == books_abs or abs_p.startswith(books_abs + os.sep)):
        raise HTTPException(status_code=400, detail="Path escapes library root")
    return os.path.relpath(abs_p, books_abs).replace("\\", "/")


def _rmdir_empty_upwards(start_abs: str) -> None:
    """rmdir start_abs and every empty parent directory up to (but not
    including) BOOKS_DIR. Tolerant — stops at first non-empty dir or OSError."""
    books_abs = os.path.abspath(BOOKS_DIR)
    cur = os.path.abspath(start_abs)
    while cur.startswith(books_abs + os.sep) and cur != books_abs:
        try:
            os.rmdir(cur)
        except OSError:
            return
        cur = os.path.dirname(cur)


@app.post("/api/admin/move", response_model=schemas.MoveResponse)
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

    books_abs = os.path.abspath(BOOKS_DIR)
    src_abs = os.path.join(books_abs, src)

    if not os.path.lexists(src_abs):
        raise HTTPException(status_code=404, detail="Source not found")

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

        # Re-derive a relative symlink target from the NEW parent — the old
        # symlink's relative target is invalid from a different parent.
        # Mirrors the commit endpoint's pattern (main.py:1534-1535).
        try:
            os.makedirs(new_parent, exist_ok=True)
            vault_path = os.path.join(DATA_DIR, hash_id)
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

STAGING_DIR = os.path.join(DATA_DIR, "staging")
COVERS_DIR = os.path.join(DATA_DIR, "covers")


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

_STAGING: dict[str, dict] = {}
_STAGING_LOCK = threading.Lock()
_STAGING_TTL_S = 3600
_MAX_UPLOAD_BYTES = 850 * 1024 * 1024
_MAX_COVER_BYTES = 5 * 1024 * 1024
_AVATAR_MAX_BYTES = 5 * 1024 * 1024

_ACCEPTED_BOOK_EXTS = {
    "fb2", "zip", "epub", "pdf", "djvu", "mobi", "azw", "azw3", "prc",
    "docx", "odt", "html", "rtf", "txt", "jpg", "jpeg",
    "mp3", "wav", "ogg", "flac", "m4a", "aac",
    "mp4", "webm", "mkv", "avi", "mov",
}
_AUDIO_EXTS = {"mp3", "wav", "ogg", "flac", "m4a", "aac"}
_VIDEO_EXTS = {"mp4", "webm", "mkv", "avi", "mov"}



def _purge_expired_staging() -> None:
    now = datetime.now(timezone.utc).timestamp()
    with _STAGING_LOCK:
        expired = [sid for sid, rec in _STAGING.items() if rec.get("expires_at", 0) < now]
        for sid in expired:
            rec = _STAGING.pop(sid, None)
            if rec:
                shutil.rmtree(rec.get("dir", ""), ignore_errors=True)
        live_dirs = {rec.get("dir") for rec in _STAGING.values()}
    # Disk reconciliation: the _STAGING map lives only in memory, so a restart or
    # crash leaves staged-but-uncommitted directories on disk that the loop above
    # can never reap (their entry is gone). Sweep any staging subdir that no live
    # entry references and that is older than the TTL — this also collects leaked
    # .cover-*/.reextract-* temp dirs. The age guard protects an in-flight upload,
    # whose _STAGING entry isn't inserted until metadata extraction finishes.
    try:
        names = os.listdir(STAGING_DIR)
    except FileNotFoundError:
        return
    for name in names:
        path = os.path.join(STAGING_DIR, name)
        if path in live_dirs or not os.path.isdir(path):
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age > _STAGING_TTL_S:
            shutil.rmtree(path, ignore_errors=True)


def _detect_format(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".fb2.zip"):
        return "fb2.zip"
    if name.endswith(".txt.zip"):
        return "txt.zip"
    if name.endswith(".md.zip"):
        return "md.zip"
    if name.endswith(".markdown.zip"):
        return "markdown.zip"
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return ext


# Suffixes that must be treated as a single unit (a format wrapped in .zip),
# not split on the last dot. Mirrors the unzip-on-read handling for those formats.
_MULTI_SUFFIXES = (".fb2.zip", ".txt.zip", ".md.zip", ".markdown.zip",
                   ".html.zip", ".htm.zip")
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _effective_suffix(name: str) -> str:
    """Filename suffix treating .fb2.zip/.txt.zip/.html.zip/etc as one unit.
    Returns the dotted suffix (e.g. '.pdf', '.fb2.zip') or '' when there's none."""
    low = name.lower()
    for suf in _MULTI_SUFFIXES:
        if low.endswith(suf):
            return suf
    return os.path.splitext(low)[1]


def _text_inner_ext(name: str) -> str:
    """Effective text extension, ignoring a trailing .zip wrapper.
    e.g. 'notes.txt.zip' -> '.txt', 'README.md' -> '.md'."""
    low = name.lower()
    if low.endswith(".zip"):
        low = low[:-4]
    return os.path.splitext(low)[1]


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


def _extract_cover_to(src_path: str, fmt: str, dest_jpg: str) -> Optional[tuple[int, int]]:
    """Run the format-appropriate cover extraction, resize to 300px-wide JPEG
    written at dest_jpg. Returns (width, height) of the saved cover, or None on
    failure."""
    tmp_dir = os.path.join(STAGING_DIR, f".cover-{uuid.uuid4().hex}")
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


def _safe_path_segment(seg: str) -> str:
    """Validate a single path segment (no separators, no traversal, no
    leading dot, non-empty). Returns the normalised segment."""
    seg = (seg or "").strip().strip("/")
    if not seg:
        raise HTTPException(status_code=400, detail="Empty path segment")
    if "/" in seg or "\\" in seg or seg in (".", "..") or seg.startswith("."):
        raise HTTPException(status_code=400, detail=f"Invalid path segment: {seg!r}")
    return seg


def _safe_subpath(sub: str) -> str:
    """Validate an optional multi-segment subpath. Empty string is OK."""
    sub = (sub or "").strip().strip("/")
    if not sub:
        return ""
    parts = [p for p in sub.split("/") if p]
    return "/".join(_safe_path_segment(p) for p in parts)


def _like_escape(s: str) -> str:
    """Escape SQL LIKE metacharacters so a literal path can be used as a
    prefix pattern. A directory named e.g. `Law_2024` must not match
    `LawX2024` via the `_` wildcard."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@app.delete("/api/admin/dirs")
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

    target_dir = os.path.abspath(os.path.join(BOOKS_DIR, sub))
    books_root = os.path.abspath(BOOKS_DIR)

    if not target_dir.startswith(books_root + os.sep) or target_dir == books_root:
        raise HTTPException(status_code=403, detail="Forbidden")

    if os.path.islink(target_dir):
        raise HTTPException(status_code=400, detail="Path is a symlink, not a directory")

    if not os.path.exists(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="Path is not a directory")

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

    # DB cleanup. SQLite foreign keys aren't enforced, so referencing rows are
    # cleared explicitly (mirrors admin_delete_book).
    for loc in locs:
        db.delete(loc)
    for h in orphan_hashes:
        book = db.query(models.Book).filter(models.Book.id == h).first()
        if book:
            db.delete(book)
    if orphan_hashes:
        db.query(models.Favorite).filter(
            models.Favorite.hash_id.in_(orphan_hashes)
        ).delete(synchronize_session=False)
        db.query(models.ReadingProgress).filter(
            models.ReadingProgress.hash_id.in_(orphan_hashes)
        ).delete(synchronize_session=False)
        db.query(models.PlaylistItem).filter(
            models.PlaylistItem.book_hash_id.in_(orphan_hashes)
        ).delete(synchronize_session=False)
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
        vault_file = os.path.join(BOOKS_DIR, ".data", h)
        if os.path.exists(vault_file):
            try:
                os.remove(vault_file)
            except OSError as e:
                errors.append(f"vault {h}: {e}")
        cover_file = os.path.join(BOOKS_DIR, ".data", "covers", f"{h}.jpg")
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


@app.get("/api/admin/dirs", response_model=schemas.DirListing)
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

    fs_dir = os.path.join(BOOKS_DIR, sub) if sub else BOOKS_DIR
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


def _validate_cover_upload(file: UploadFile) -> None:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Invalid image file")


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
    _ensure_writable_dir(COVERS_DIR)
    dest = os.path.join(COVERS_DIR, f"{hash_id}.jpg")
    im.save(dest, format="JPEG", quality=85)
    return _cover_url_for(hash_id) or f"/api/covers/{hash_id}"


@app.put("/api/admin/books/{hash_id}/cover", response_model=schemas.CoverUpdateResponse)
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


@app.post("/api/admin/books/{hash_id}/cover/reextract", response_model=schemas.CoverUpdateResponse)
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
    symlink_fs = os.path.join(BOOKS_DIR, loc_row[0])
    real_path = os.path.realpath(symlink_fs)
    if not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Book file missing on disk")
    fmt = _detect_format(book.original_filename or loc_row[0])
    _ensure_writable_dir(COVERS_DIR)
    _ensure_writable_dir(STAGING_DIR)
    dest = os.path.join(COVERS_DIR, f"{hash_id}.jpg")
    # Run extraction against a copy that retains the original extension so
    # ebook-meta's dispatch works (the vault file is bare hex).
    tmp_dir = os.path.join(STAGING_DIR, f".reextract-{uuid.uuid4().hex}")
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


@app.post("/api/admin/books/upload")
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
    _ensure_writable_dir(STAGING_DIR)
    staging_id = uuid.uuid4().hex
    sdir = os.path.join(STAGING_DIR, staging_id)
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
            yield _sse_event("log", _log_entry("info", f"POST /api/admin/books/upload  multipart/form-data"))
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

            with _STAGING_LOCK:
                _STAGING[staging_id] = {
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


@app.get("/api/admin/books/upload/{staging_id}/cover.jpg")
def admin_staging_cover(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    rec = _STAGING.get(staging_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Staging not found")
    cover = os.path.join(rec["dir"], "cover.jpg")
    if not os.path.exists(cover):
        raise HTTPException(status_code=404, detail="No cover for staging")
    return FileResponse(cover, media_type="image/jpeg")


@app.post("/api/admin/books/upload/{staging_id}/cover", response_model=schemas.CoverUpdateResponse)
async def admin_staging_cover_override(
    staging_id: str,
    file: UploadFile = File(...),
    _admin: models.User = Depends(require_admin),
):
    rec = _STAGING.get(staging_id)
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
    rec = _STAGING.get(staging_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Staging not found")
    path = os.path.join(rec["dir"], rec["filename"])
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Staging file missing")
    return path


@app.get("/api/admin/books/upload/{staging_id}/file")
def admin_staging_file(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    """Serve the raw staged file for embedded preview (PDF, EPUB, image)."""
    return FileResponse(_get_staging_file(staging_id))


@app.get("/api/admin/books/upload/{staging_id}/fb2-content")
async def admin_staging_fb2_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    # No stored-extension gate: the admin may be relabelling a mislabeled file,
    # so we let the reader decide. A genuine FB2 parses; anything else 422s.
    file_path = _get_staging_file(staging_id)
    try:
        xml_bytes = _read_fb2_bytes(file_path)
        return _convert_fb2(xml_bytes)
    except HTTPException:
        raise
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    except Exception:
        raise HTTPException(status_code=422, detail="Not a valid FB2 file")


@app.get("/api/admin/books/upload/{staging_id}/md-content")
async def admin_staging_md_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    file_path = _get_staging_file(staging_id)
    text = _read_text_file(file_path)
    inner = _text_inner_ext(file_path)
    if inner == ".txt":
        return _convert_txt(text)
    elif inner in CODE_EXTENSIONS:
        return _convert_code(text, inner[1:])
    return _convert_md(text)


@app.get("/api/admin/books/upload/{staging_id}/html-content")
async def admin_staging_html_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    # No stored-extension gate (see fb2-content): let the reader decide.
    file_path = _get_staging_file(staging_id)
    try:
        data = _read_html_bytes(file_path)
        return _convert_html(data)
    except HTTPException:
        raise
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    except Exception:
        raise HTTPException(status_code=422, detail="Not a valid HTML file")


@app.get("/api/admin/books/upload/{staging_id}/djvu-metadata")
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


@app.get("/api/admin/books/upload/{staging_id}/djvu-outline")
async def admin_staging_djvu_outline(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    # No stored-extension gate (see fb2-content); gate on the DjVu magic.
    file_path = _get_staging_file(staging_id)
    if _staged_reads_as(file_path, ".djvu") is not True:
        raise HTTPException(status_code=422, detail="Not a valid DjVu file")
    try:
        return {"toc": extract_djvu_outline(file_path)}
    except Exception:
        raise HTTPException(status_code=422, detail="Not a valid DjVu file")


@app.get("/api/admin/books/upload/{staging_id}/djvu-page")
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


@app.delete("/api/admin/books/upload/{staging_id}")
async def admin_cancel_staging(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    rec = _STAGING.pop(staging_id, None)
    if rec:
        shutil.rmtree(rec.get("dir", ""), ignore_errors=True)
    return {"cancelled": staging_id}


@app.post("/api/admin/books/upload/touch")
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
    with _STAGING_LOCK:
        for sid, rec in _STAGING.items():
            if sid in wanted and rec.get("owner_id") == admin.id:
                rec["expires_at"] = now + _STAGING_TTL_S
                touched += 1
    return {"touched": touched}


def _safe_under_books(path: str) -> str:
    """Resolve a user-supplied relative path under BOOKS_DIR with the standard
    traversal guard, and reject the infra entries in _TOPDIR_SKIPLIST. Returns the
    absolute path (existence is the caller's concern)."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    base = os.path.abspath(BOOKS_DIR)
    target = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if target != base and not target.startswith(base + os.sep):
        raise HTTPException(status_code=403, detail="Forbidden")
    # abspath() is purely lexical, so a symlinked *directory component* (e.g.
    # Unsorted/link-out → /etc) would slip past the prefix check and let the
    # importer read/copy files from outside the tree. realpath() resolves the
    # symlinks; the library's own book symlinks still resolve within BOOKS_DIR
    # (into .data), so legitimate paths are unaffected.
    real_base = os.path.realpath(BOOKS_DIR)
    real_target = os.path.realpath(target)
    if real_target != real_base and not real_target.startswith(real_base + os.sep):
        raise HTTPException(status_code=403, detail="Forbidden")
    rel = os.path.relpath(target, base)
    if rel != "." and rel.split(os.sep, 1)[0] in _TOPDIR_SKIPLIST:
        raise HTTPException(status_code=403, detail="Forbidden")
    return target


def _is_importable_file(abs_path: str) -> bool:
    """A plain (non-symlink) regular file with an accepted book extension. Symlinks
    are committed books already in the vault, so they're not importable."""
    if os.path.islink(abs_path) or not os.path.isfile(abs_path):
        return False
    return _detect_format(os.path.basename(abs_path)).split(".")[-1] in _ACCEPTED_BOOK_EXTS


_IMPORTABLE_CAP = 200


@app.post("/api/admin/books/importable")
async def admin_importable(
    payload: schemas.ImportableRequest,
    _admin: models.User = Depends(require_admin),
):
    """Expand a Browse selection (files and/or directories) into the concrete list
    of importable book files — plain (non-symlink) files with an accepted extension,
    recursively for directories. Lets the Browse 'Import to library' action turn a
    folder tick into file paths."""
    base = os.path.abspath(BOOKS_DIR)
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


@app.post("/api/admin/books/stage-from-path")
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

    _ensure_writable_dir(STAGING_DIR)
    staging_id = uuid.uuid4().hex
    sdir = os.path.join(STAGING_DIR, staging_id)
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

        with _STAGING_LOCK:
            _STAGING[staging_id] = {
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


@app.post("/api/admin/books/commit", response_model=schemas.AdminBookDetail)
async def admin_commit_book(
    payload: schemas.UploadCommitRequest,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Finalise a staged upload: move file into the CAS vault, link from
    /<top>/<sub>/<filename>, register Book + BookLocation, return AdminBookDetail."""
    _purge_expired_staging()
    rec = _STAGING.get(payload.staging_id)
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
    abs_target_dir = os.path.abspath(os.path.join(BOOKS_DIR, rel_dir))
    abs_target = os.path.abspath(os.path.join(BOOKS_DIR, rel_path))
    if not abs_target.startswith(os.path.abspath(BOOKS_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Path escapes library root")

    # If a registered book *just* showed up at this hash via a concurrent
    # commit (unlikely, but cheap to check), bail out.
    if db.query(models.Book).filter(models.Book.id == file_hash).first():
        shutil.rmtree(rec["dir"], ignore_errors=True)
        _STAGING.pop(payload.staging_id, None)
        raise HTTPException(status_code=409, detail="Duplicate hash already registered")

    if os.path.lexists(abs_target):
        raise HTTPException(status_code=409, detail=f"A file already exists at {rel_path}")

    # 1. Move the staged file into the vault.
    vault_path = os.path.join(DATA_DIR, file_hash)
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
        _ensure_writable_dir(COVERS_DIR)
        cover_dest = os.path.join(COVERS_DIR, f"{file_hash}.jpg")
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
    with _STAGING_LOCK:
        _STAGING.pop(payload.staging_id, None)

    return _book_to_admin_detail(book, db)


@app.post("/api/admin/integrity/verify/{hash_id}", response_model=schemas.IntegrityCheckResult)
async def admin_verify_book(
    hash_id: str,
    mode: str = "quick",
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if mode not in ("quick", "full"):
        raise HTTPException(status_code=400, detail="mode must be 'quick' or 'full'")
    return await asyncio.to_thread(_verify_book_sync, hash_id, mode, db)


# ---------- Bulk integrity jobs ----------

INTEGRITY_JOBS: dict[str, dict] = {}
INTEGRITY_JOBS_LOCK = threading.Lock()
INTEGRITY_JOB_TTL_SECONDS = 3600
INTEGRITY_ALL_RESULTS_CAP = 5000


def _sweep_expired_jobs() -> None:
    now = datetime.now(timezone.utc)
    with INTEGRITY_JOBS_LOCK:
        for jid in list(INTEGRITY_JOBS.keys()):
            job = INTEGRITY_JOBS[jid]
            fin = job.get("finished_at")
            if not fin or job["status"] == "running":
                continue
            try:
                finished = datetime.strptime(fin, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if (now - finished).total_seconds() > INTEGRITY_JOB_TTL_SECONDS:
                del INTEGRITY_JOBS[jid]


def _running_job_id() -> Optional[str]:
    with INTEGRITY_JOBS_LOCK:
        for jid, job in INTEGRITY_JOBS.items():
            if job["status"] == "running":
                return jid
    return None


def _job_summary(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "mode": job["mode"],
        "total": job["total"],
        "processed": job["processed"],
        "ok_count": job["ok_count"],
        "fail_count": job["fail_count"],
        "started_at": job["started_at"],
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
    }


async def _run_integrity_job(job_id: str) -> None:
    """Background worker. One book at a time, each in its own thread + session."""
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
    if job is None:
        return
    hash_ids = job["hash_ids"]
    mode = job["mode"]

    def _verify_one(hid: str) -> dict:
        session = SessionLocal()
        try:
            return _verify_book_sync(hid, mode, session)
        finally:
            session.close()

    try:
        for hid in hash_ids:
            if job.get("cancel_requested"):
                with INTEGRITY_JOBS_LOCK:
                    job["status"] = "cancelled"
                    job["finished_at"] = _now_iso()
                return
            try:
                result = await asyncio.to_thread(_verify_one, hid)
            except Exception as e:
                logging.exception("verify failed for %s", hid)
                result = {
                    "hash_id": hid, "mode": mode, "ok": False, "error": "exception",
                    "checks": [{"name": "worker", "ok": False, "detail": str(e)}],
                    "verified_at": _now_iso(),
                    "title": None, "original_filename": None, "db_update_failed": True,
                }
            with INTEGRITY_JOBS_LOCK:
                job["processed"] += 1
                if result["ok"]:
                    job["ok_count"] += 1
                else:
                    job["fail_count"] += 1
                    job["failures"].append(result)
                if len(job["all_results"]) < INTEGRITY_ALL_RESULTS_CAP:
                    job["all_results"].append(result)
                else:
                    job["all_results_truncated"] = True
        with INTEGRITY_JOBS_LOCK:
            if job["status"] == "running":
                job["status"] = "done"
                job["finished_at"] = _now_iso()
    except Exception as e:
        logging.exception("integrity job %s crashed", job_id)
        with INTEGRITY_JOBS_LOCK:
            job["status"] = "error"
            job["error"] = str(e)
            job["finished_at"] = _now_iso()


@app.post("/api/admin/integrity/jobs", response_model=schemas.IntegrityJobSummary)
async def admin_start_integrity_job(
    payload: schemas.IntegrityJobCreate,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _sweep_expired_jobs()
    if payload.scope not in ("all", "hash_ids"):
        raise HTTPException(status_code=400, detail="scope must be 'all' or 'hash_ids'")
    if payload.mode not in ("quick", "full"):
        raise HTTPException(status_code=400, detail="mode must be 'quick' or 'full'")

    if payload.scope == "all":
        hash_ids = [r[0] for r in db.query(models.Book.id).all()]
    else:
        hash_ids = list(dict.fromkeys(payload.hash_ids or []))  # de-dup, preserve order
        if not hash_ids:
            raise HTTPException(status_code=400, detail="hash_ids must be non-empty")

    running = _running_job_id()
    if running:
        raise HTTPException(
            status_code=409,
            detail={"reason": "job_running", "running_job_id": running},
        )

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "running",
        "mode": payload.mode,
        "total": len(hash_ids),
        "processed": 0,
        "ok_count": 0,
        "fail_count": 0,
        "started_at": _now_iso(),
        "finished_at": None,
        "cancel_requested": False,
        "error": None,
        "hash_ids": hash_ids,
        "failures": [],
        "all_results": [],
        "all_results_truncated": False,
    }
    with INTEGRITY_JOBS_LOCK:
        INTEGRITY_JOBS[job_id] = job

    asyncio.create_task(_run_integrity_job(job_id))
    return _job_summary(job)


@app.get("/api/admin/integrity/jobs")
async def admin_list_integrity_jobs(_admin: models.User = Depends(require_admin)):
    _sweep_expired_jobs()
    with INTEGRITY_JOBS_LOCK:
        jobs = [_job_summary(j) for j in INTEGRITY_JOBS.values()]
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {"jobs": jobs}


@app.get("/api/admin/integrity/jobs/{job_id}", response_model=schemas.IntegrityJobDetail)
async def admin_get_integrity_job(
    job_id: str,
    include: str = "failures",
    _admin: models.User = Depends(require_admin),
):
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        detail = _job_summary(job)
        detail["failures"] = list(job["failures"])
        if include == "all":
            detail["all_results"] = list(job["all_results"])
        else:
            detail["all_results"] = None
        detail["all_results_truncated"] = bool(job.get("all_results_truncated"))
    return detail


@app.delete("/api/admin/integrity/jobs/{job_id}", response_model=schemas.IntegrityJobSummary)
async def admin_cancel_integrity_job(
    job_id: str,
    _admin: models.User = Depends(require_admin),
):
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] == "running":
            job["cancel_requested"] = True
        return _job_summary(job)


@app.get("/api/files/{path:path}")
async def get_file(
    request: Request,
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = os.path.join(BOOKS_DIR, path)
    if not os.path.abspath(file_path).startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    assert_can_read_path(file_path, current_user, db)
    _record_usage_event(
        request, "book_open",
        user=current_user,
        hash_id=_resolve_vault_hash(file_path),
        path=path,
    )
    return FileResponse(file_path)

def sanitize_fb2_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR, exists, and is an FB2 (or .fb2.zip) file."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    if not (lower.endswith(".fb2") or lower.endswith(".fb2.zip")):
        raise HTTPException(status_code=400, detail="Not an FB2 file")
    return target_path


def _read_fb2_bytes(file_path: str) -> bytes:
    if file_path.lower().endswith(".zip"):
        with zipfile.ZipFile(file_path) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".fb2"):
                    return zf.read(name)
        raise HTTPException(status_code=422, detail="No .fb2 entry inside zip")
    with open(file_path, "rb") as f:
        return f.read()


_FB2_NS = "{http://www.gribuser.ru/xml/fictionbook/2.0}"
_XLINK_NS = "{http://www.w3.org/1999/xlink}"


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


class _Fb2Renderer:
    SAFE_URL_RE = re.compile(r"^(https?:|mailto:|#|/)", re.IGNORECASE)

    def __init__(self, binaries: Dict[str, str], anchored: bool = True, collect_toc: bool = False):
        self.binaries = binaries
        self.anchored = anchored
        self.anchor = 0
        self.collect_toc = collect_toc
        self.toc: List[Dict[str, Any]] = []
        self._toc_parents: List[List[Dict[str, Any]]] = [self.toc]

    def _attr(self) -> str:
        if not self.anchored:
            return ""
        n = self.anchor
        self.anchor += 1
        return f' id="fb2-a-{n}" data-anchor="{n}"'

    def _last_anchor(self):
        return self.anchor - 1 if self.anchored and self.anchor > 0 else None

    _BLOCK_TAGS = {"p", "subtitle", "v", "empty-line", "title", "stanza", "epigraph"}

    @classmethod
    def _plain_text(cls, el) -> str:
        # itertext() concatenates text without separators, so adjacent block
        # children like <p>A</p><p>B</p> would render as "AB". Walk the tree
        # ourselves and insert a space after each block child while preserving
        # inline runs (so <p>Hi <em>world</em>!</p> stays "Hi world!").
        out: List[str] = []

        def walk(node):
            if node.text:
                out.append(node.text)
            for child in node:
                walk(child)
                if child.tail:
                    out.append(child.tail)
                if _strip_ns(child.tag) in cls._BLOCK_TAGS:
                    out.append(" ")

        walk(el)
        return " ".join("".join(out).split())

    def _href(self, el) -> str:
        return el.get(_XLINK_NS + "href") or el.get("href") or ""

    def _safe_href(self, href: str) -> str:
        return href if self.SAFE_URL_RE.match(href) else "#"

    def render_inline(self, el) -> str:
        out = []
        if el.text:
            out.append(_html_escape(el.text))
        for child in el:
            tag = _strip_ns(child.tag)
            inner = self.render_inline(child)
            if tag == "emphasis":
                out.append(f"<em>{inner}</em>")
            elif tag == "strong":
                out.append(f"<strong>{inner}</strong>")
            elif tag == "strikethrough":
                out.append(f"<s>{inner}</s>")
            elif tag == "sub":
                out.append(f"<sub>{inner}</sub>")
            elif tag == "sup":
                out.append(f"<sup>{inner}</sup>")
            elif tag == "code":
                out.append(f"<code>{inner}</code>")
            elif tag == "a":
                href = self._safe_href(self._href(child))
                note_type = child.get("type") or ""
                cls = "fb2-link fb2-note" if note_type == "note" else "fb2-link"
                out.append(f'<a href="{_html_escape(href)}" class="{cls}">{inner}</a>')
            elif tag == "image":
                bid = self._href(child).lstrip("#")
                src = self.binaries.get(bid)
                if src:
                    out.append(f'<img src="{src}" class="fb2-inline-img" alt="" />')
            elif tag == "style":
                out.append(inner)
            elif tag == "empty-line":
                out.append("<br />")
            else:
                out.append(inner)
            if child.tail:
                out.append(_html_escape(child.tail))
        return "".join(out)

    def _title_text(self, el) -> str:
        # <title> usually contains <p> children
        parts = []
        for c in el:
            if _strip_ns(c.tag) == "p":
                parts.append(self.render_inline(c))
            elif _strip_ns(c.tag) == "empty-line":
                parts.append("<br />")
        if not parts:
            parts.append(self.render_inline(el))
        return " ".join(parts)

    def render_block(self, el, depth: int = 0) -> str:
        tag = _strip_ns(el.tag)

        if tag == "section":
            entry = None
            if self.collect_toc:
                entry = {"title": "", "anchor": None, "children": []}
                self._toc_parents[-1].append(entry)
                self._toc_parents.append(entry["children"])
            try:
                parts = []
                for c in el:
                    ctag = _strip_ns(c.tag)
                    if ctag == "title":
                        level = min(max(depth + 1, 2), 6)
                        attr = self._attr()
                        if entry is not None:
                            entry["anchor"] = self._last_anchor()
                            entry["title"] = self._plain_text(c)
                        parts.append(
                            f'<h{level}{attr} class="fb2-section-title">'
                            f'{self._title_text(c)}</h{level}>'
                        )
                    elif ctag == "section":
                        parts.append(self.render_block(c, depth + 1))
                    else:
                        parts.append(self.render_block(c, depth))
                return f'<section class="fb2-section">{"".join(parts)}</section>'
            finally:
                if self.collect_toc:
                    self._toc_parents.pop()

        attr = self._attr()

        if tag == "p":
            return f'<p{attr} class="fb2-p">{self.render_inline(el)}</p>'
        if tag == "subtitle":
            return f'<h4{attr} class="fb2-subtitle">{self._title_text(el)}</h4>'
        if tag == "empty-line":
            return f'<div{attr} class="fb2-empty-line"></div>'
        if tag == "image":
            bid = self._href(el).lstrip("#")
            src = self.binaries.get(bid)
            if not src:
                return ""
            return f'<div{attr} class="fb2-image-wrap"><img src="{src}" class="fb2-image" alt="" /></div>'
        if tag in ("epigraph", "cite"):
            inner_parts = []
            for c in el:
                if _strip_ns(c.tag) in ("p", "poem", "subtitle", "empty-line", "text-author"):
                    inner_parts.append(self.render_block(c, depth))
            return f'<blockquote{attr} class="fb2-{tag}">{"".join(inner_parts)}</blockquote>'
        if tag == "text-author":
            return f'<p{attr} class="fb2-text-author">{self.render_inline(el)}</p>'
        if tag == "poem":
            inner = []
            for c in el:
                ctag = _strip_ns(c.tag)
                if ctag == "title":
                    inner.append(f'<div class="fb2-poem-title">{self._title_text(c)}</div>')
                elif ctag == "stanza":
                    lines = []
                    for v in c:
                        vtag = _strip_ns(v.tag)
                        if vtag == "v":
                            lines.append(f'<div class="fb2-v">{self.render_inline(v)}</div>')
                        elif vtag == "title":
                            lines.append(f'<div class="fb2-stanza-title">{self._title_text(v)}</div>')
                    inner.append(f'<div class="fb2-stanza">{"".join(lines)}</div>')
                elif ctag == "text-author":
                    inner.append(f'<div class="fb2-text-author">{self.render_inline(c)}</div>')
                elif ctag == "epigraph":
                    inner.append(self.render_block(c, depth))
            return f'<div{attr} class="fb2-poem">{"".join(inner)}</div>'
        # Unknown block: render its inline contents in a div so text isn't lost
        return f'<div{attr} class="fb2-other">{self.render_inline(el)}</div>'


def _extract_binaries(root) -> Dict[str, str]:
    """Build {binary-id: data:URI} for all <binary> elements."""
    out: Dict[str, str] = {}
    for b in root.iter(_FB2_NS + "binary"):
        bid = b.get("id")
        ctype = b.get("content-type") or "application/octet-stream"
        if not bid or not b.text:
            continue
        # Re-encode to drop whitespace inside the base64 blob
        try:
            raw = base64.b64decode(b.text)
            out[bid] = f"data:{ctype};base64,{base64.b64encode(raw).decode('ascii')}"
        except Exception:
            continue
    return out


def _extract_metadata(root) -> Dict[str, Any]:
    desc = root.find(_FB2_NS + "description")
    title = ""
    authors: List[str] = []
    if desc is not None:
        ti = desc.find(_FB2_NS + "title-info")
        if ti is not None:
            book_title = ti.find(_FB2_NS + "book-title")
            if book_title is not None and book_title.text:
                title = book_title.text.strip()
            for a in ti.findall(_FB2_NS + "author"):
                first = a.findtext(_FB2_NS + "first-name", default="").strip()
                middle = a.findtext(_FB2_NS + "middle-name", default="").strip()
                last = a.findtext(_FB2_NS + "last-name", default="").strip()
                nick = a.findtext(_FB2_NS + "nickname", default="").strip()
                full = " ".join(p for p in (first, middle, last) if p) or nick
                if full:
                    authors.append(full)
    return {"title": title, "authors": authors}


def _render_note_section(section, binaries: Dict[str, str]) -> str:
    """Render the contents of a single <section> from a notes body, dropping
    its <title> (which is usually just the note number, redundant with the
    inline marker the user clicked)."""
    note_renderer = _Fb2Renderer(binaries, anchored=False)
    parts = []
    for c in section:
        if _strip_ns(c.tag) == "title":
            continue
        parts.append(note_renderer.render_block(c))
    return "".join(parts)


def _convert_fb2(xml_bytes: bytes) -> Dict[str, Any]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise HTTPException(status_code=422, detail=f"Invalid FB2 XML: {e}")
    binaries = _extract_binaries(root)
    renderer = _Fb2Renderer(binaries, collect_toc=True)
    bodies_html: List[str] = []
    notes: Dict[str, str] = {}
    for body in root.findall(_FB2_NS + "body"):
        raw_name = body.get("name") or ""
        if raw_name:
            # Footnotes / comments / etc. — collect by section id for tooltip
            # rendering on the frontend, do not append to the main body HTML.
            for section in body.iter(_FB2_NS + "section"):
                sid = section.get("id")
                if sid:
                    notes[sid] = _render_note_section(section, binaries)
            continue
        parts = []
        # body may have its own <title> and <epigraph> before sections
        for c in body:
            ctag = _strip_ns(c.tag)
            if ctag == "title":
                attr = renderer._attr()
                if renderer.collect_toc:
                    renderer.toc.append({
                        "title": renderer._plain_text(c),
                        "anchor": renderer._last_anchor(),
                        "children": [],
                    })
                parts.append(
                    f'<h2{attr} class="fb2-body-title">'
                    f'{renderer._title_text(c)}</h2>'
                )
            else:
                parts.append(renderer.render_block(c))
        bodies_html.append(f'<div class="fb2-body">{"".join(parts)}</div>')
    meta = _extract_metadata(root)
    return {
        "title": meta["title"],
        "authors": meta["authors"],
        "html": "".join(bodies_html),
        "anchor_count": renderer.anchor,
        "notes": notes,
        "toc": renderer.toc,
    }


def _extract_annotation_html(root) -> str:
    desc = root.find(_FB2_NS + "description")
    if desc is None:
        return ""
    ti = desc.find(_FB2_NS + "title-info")
    if ti is None:
        return ""
    annotation = ti.find(_FB2_NS + "annotation")
    if annotation is None:
        return ""
    renderer = _Fb2Renderer({}, anchored=False)
    return "".join(renderer.render_block(c) for c in annotation)


@app.get("/api/fb2-content")
async def fb2_content(
    request: Request,
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_fb2_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        xml_bytes = _read_fb2_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    _record_usage_event(
        request, "book_open",
        user=current_user,
        hash_id=_resolve_vault_hash(file_path),
        path=path,
    )
    return _convert_fb2(xml_bytes)


@app.get("/api/fb2-metadata")
async def fb2_metadata(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_fb2_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        xml_bytes = _read_fb2_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise HTTPException(status_code=422, detail=f"Invalid FB2 XML: {e}")
    meta = _extract_metadata(root)
    return {
        "title": meta["title"],
        "authors": meta["authors"],
        "annotation_html": _extract_annotation_html(root),
    }


# ---------------- Markdown / plain-text viewer ----------------

CODE_EXTENSIONS = {
    ".py", ".c", ".cpp", ".h", ".hpp", ".js", ".ts", ".jsx", ".tsx",
    ".lua", ".sh", ".bash", ".rs", ".go", ".java", ".css", ".scss",
    ".json", ".xml", ".yaml", ".yml", ".sql", ".ini"
}

def sanitize_text_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR, exists, and is a .md/.markdown/.txt
    (optionally .zip-wrapped) or code file."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    ext = os.path.splitext(lower)[1]
    if not (lower.endswith((".md", ".markdown", ".txt", ".txt.zip", ".md.zip", ".markdown.zip"))
            or ext in CODE_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Not a Markdown, text, or supported code file")
    return target_path


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


def _read_text_file(file_path: str) -> str:
    raw = _read_text_bytes(file_path)
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_MD_SAFE_URL_RE = re.compile(r"^(https?:|mailto:|/|#|\.\.?/)", re.IGNORECASE)


class _MdRenderer:
    """Minimal CommonMark-ish renderer. Handles ATX/setext headings, paragraphs,
    fenced code, blockquotes, flat lists, horizontal rules, and inline emphasis/
    code/links/images. Raw HTML in source is escaped, never passed through."""

    _ATX_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
    _FENCE_RE = re.compile(r"^\s{0,3}(```+|~~~+)\s*([^\s`]*)\s*$")
    _HR_RE = re.compile(r"^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})\s*$")
    _BQ_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
    _UL_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
    _OL_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
    _SETEXT_H1_RE = re.compile(r"^=+\s*$")
    _SETEXT_H2_RE = re.compile(r"^-+\s*$")

    def __init__(self, collect_toc: bool = True):
        self.collect_toc = collect_toc
        self.anchor = 0
        self.toc_flat: List[Dict[str, Any]] = []

    def _attr(self):
        n = self.anchor
        self.anchor += 1
        return f' id="md-a-{n}" data-anchor="{n}"', n

    @staticmethod
    def _strip_html(html: str) -> str:
        return re.sub(r"<[^>]+>", "", html)

    @classmethod
    def _safe_url(cls, url: str) -> str:
        url = url.strip()
        return url if _MD_SAFE_URL_RE.match(url) else "#"

    def _inline(self, text: str) -> str:
        placeholders: List[str] = []

        def stash(html: str) -> str:
            placeholders.append(html)
            return f"\x00{len(placeholders) - 1}\x00"

        # Inline code first (its contents must NOT have other rules applied).
        def code_repl(m):
            return stash(f"<code>{_html_escape(m.group(2))}</code>")
        text = re.sub(r"(`+)([^`\n]+?)\1", code_repl, text)

        # Images
        def image_repl(m):
            alt, url = m.group(1), m.group(2)
            return stash(
                f'<img src="{_html_escape(self._safe_url(url))}" '
                f'alt="{_html_escape(alt)}" class="md-image" />'
            )
        text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", image_repl, text)

        # Links
        def link_repl(m):
            label, url = m.group(1), m.group(2)
            return stash(
                f'<a href="{_html_escape(self._safe_url(url))}" class="md-link">'
                f'{self._inline(label)}</a>'
            )
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", link_repl, text)

        # Autolinks <http://...> / <mailto:...>
        def auto_repl(m):
            url = m.group(1)
            return stash(
                f'<a href="{_html_escape(url)}" class="md-link">{_html_escape(url)}</a>'
            )
        text = re.sub(r"<((?:https?:|mailto:)[^>\s]+)>", auto_repl, text)

        text = _html_escape(text)

        # Hard line breaks (two+ trailing spaces before \n) — convert before
        # collapsing newlines elsewhere.
        text = re.sub(r" {2,}\n", "<br />\n", text)

        # Bold, then italic. Order matters so ** doesn't get eaten as two * runs.
        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"(?<![\*\w])\*(?=\S)([^\*\n]+?)(?<=\S)\*(?![\*\w])", r"<em>\1</em>", text)
        text = re.sub(r"(?<![_\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![_\w])", r"<em>\1</em>", text)
        text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", text, flags=re.S)

        return re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)

    def _emit_heading(self, level: int, raw: str) -> str:
        attr, n = self._attr()
        inner = self._inline(raw.strip())
        if self.collect_toc:
            self.toc_flat.append({
                "title": self._strip_html(inner),
                "level": level,
                "anchor": n,
            })
        return f'<h{level}{attr} class="md-h{level}">{inner}</h{level}>'

    def _emit_paragraph(self, lines: List[str]) -> str:
        attr, _ = self._attr()
        joined = "\n".join(lines)
        content = self._inline(joined)
        # Soft line breaks → single space (CommonMark default). Skip newlines
        # that were already promoted to <br />.
        content = re.sub(r"(?<!<br />)\n", " ", content)
        return f'<p{attr} class="md-p">{content}</p>'

    def _emit_codeblock(self, code: str, lang: str = "") -> str:
        attr, _ = self._attr()
        lang_class = f' class="language-{_html_escape(lang)}"' if lang else ""
        return (
            f'<pre{attr} class="md-codeblock"><code{lang_class}>'
            f'{_html_escape(code)}</code></pre>'
        )

    def _emit_blockquote(self, lines: List[str]) -> str:
        attr, _ = self._attr()
        inner = "<br />".join(self._inline(l) for l in lines)
        return f'<blockquote{attr} class="md-blockquote">{inner}</blockquote>'

    def _emit_list(self, ordered: bool, items: List[List[str]]) -> str:
        attr, _ = self._attr()
        tag = "ol" if ordered else "ul"
        parts = []
        for item_lines in items:
            content = self._inline(" ".join(l.strip() for l in item_lines))
            parts.append(f'<li class="md-li">{content}</li>')
        return f'<{tag}{attr} class="md-{tag}">{"".join(parts)}</{tag}>'

    def _emit_hr(self) -> str:
        attr, _ = self._attr()
        return f'<hr{attr} class="md-hr" />'

    def _is_block_start(self, line: str, nxt: str) -> bool:
        if self._ATX_RE.match(line): return True
        if self._FENCE_RE.match(line): return True
        if self._HR_RE.match(line): return True
        if self._BQ_RE.match(line): return True
        if self._UL_RE.match(line): return True
        if self._OL_RE.match(line): return True
        if nxt and (self._SETEXT_H1_RE.match(nxt) or self._SETEXT_H2_RE.match(nxt)):
            return True
        return False

    def render(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        lines = text.split("\n")
        n = len(lines)
        out: List[str] = []
        i = 0
        while i < n:
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            m = self._FENCE_RE.match(line)
            if m:
                fence, lang = m.group(1), m.group(2)
                i += 1
                code_lines: List[str] = []
                while i < n and not lines[i].strip().startswith(fence):
                    code_lines.append(lines[i])
                    i += 1
                if i < n:
                    i += 1  # consume closing fence
                out.append(self._emit_codeblock("\n".join(code_lines), lang))
                continue

            m = self._ATX_RE.match(line)
            if m:
                out.append(self._emit_heading(len(m.group(1)), m.group(2)))
                i += 1
                continue

            if i + 1 < n:
                nxt = lines[i + 1]
                if self._SETEXT_H1_RE.match(nxt) and len(nxt.strip()) >= 1:
                    out.append(self._emit_heading(1, stripped))
                    i += 2
                    continue
                if self._SETEXT_H2_RE.match(nxt) and len(nxt.strip()) >= 2:
                    out.append(self._emit_heading(2, stripped))
                    i += 2
                    continue

            if self._HR_RE.match(line):
                out.append(self._emit_hr())
                i += 1
                continue

            if self._BQ_RE.match(line):
                bq: List[str] = []
                while i < n and self._BQ_RE.match(lines[i]):
                    bq.append(self._BQ_RE.match(lines[i]).group(1))
                    i += 1
                out.append(self._emit_blockquote(bq))
                continue

            m_ul = self._UL_RE.match(line)
            m_ol = self._OL_RE.match(line)
            if m_ul or m_ol:
                ordered = m_ol is not None
                items: List[List[str]] = []
                while i < n:
                    cur = lines[i]
                    if not cur.strip():
                        break
                    mm = self._OL_RE.match(cur) if ordered else self._UL_RE.match(cur)
                    if mm:
                        items.append([mm.group(3)])
                        i += 1
                        continue
                    # Continuation: indented line under last item
                    if items and cur.startswith(" "):
                        items[-1].append(cur)
                        i += 1
                        continue
                    break
                out.append(self._emit_list(ordered, items))
                continue

            # Paragraph
            para = [line]
            i += 1
            while i < n and lines[i].strip():
                nxt = lines[i + 1] if i + 1 < n else ""
                if self._is_block_start(lines[i], nxt):
                    break
                para.append(lines[i])
                i += 1
            out.append(self._emit_paragraph(para))

        return "".join(out)


def _nest_toc(flat: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert flat [{title, level, anchor}] into a nested tree of
    {title, anchor, children}."""
    root: List[Dict[str, Any]] = []
    stack: List[tuple] = []  # (level, children_list)
    for item in flat:
        entry = {"title": item["title"], "anchor": item["anchor"], "children": []}
        while stack and stack[-1][0] >= item["level"]:
            stack.pop()
        (stack[-1][1] if stack else root).append(entry)
        stack.append((item["level"], entry["children"]))
    return root


def _extract_md_title(toc_flat: List[Dict[str, Any]]) -> str:
    for item in toc_flat:
        if item["level"] == 1 and item["title"].strip():
            return item["title"].strip()
    return ""


def _convert_md(text: str) -> Dict[str, Any]:
    renderer = _MdRenderer(collect_toc=True)
    html = renderer.render(text)
    return {
        "title": _extract_md_title(renderer.toc_flat),
        "html": html,
        "raw": text,
        "toc": _nest_toc(renderer.toc_flat),
        "anchor_count": renderer.anchor,
    }


def _convert_code(text: str, lang: str) -> Dict[str, Any]:
    """Source-code viewer: emit a single <pre class="md-codeblock"><code> with
    the file contents HTML-escaped. Bypasses the markdown parser so that lines
    starting with ``` (e.g. embedded in docstrings) don't terminate the block."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lang_class = f' class="language-{_html_escape(lang)}"' if lang else ""
    html = (
        f'<pre id="md-a-0" data-anchor="0" class="md-codeblock">'
        f'<code{lang_class}>{_html_escape(normalized)}</code></pre>'
    )
    return {"title": "", "html": html, "raw": normalized, "toc": [], "anchor_count": 1}


def _render_code_snippet_html(text: str, lang: str) -> str:
    lang_class = f' class="language-{_html_escape(lang)}"' if lang else ""
    return (
        f'<pre class="md-codeblock"><code{lang_class}>'
        f'{_html_escape(text)}</code></pre>'
    )


def _convert_txt(text: str) -> Dict[str, Any]:
    """Plain-text viewer: each blank-line-separated block becomes one anchored
    <pre>, preserving the author's line wrapping and any ASCII layout."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    parts: List[str] = []
    anchor = 0
    for block in re.split(r"\n\s*\n", normalized):
        if not block.strip():
            continue
        escaped = _html_escape(block.rstrip("\n"))
        parts.append(
            f'<pre id="md-a-{anchor}" data-anchor="{anchor}" class="md-txt-block">{escaped}</pre>'
        )
        anchor += 1
    return {"title": "", "html": "".join(parts), "raw": normalized, "toc": [], "anchor_count": anchor}


@app.get("/api/md-content")
async def md_content(
    request: Request,
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_text_path(path)
    assert_can_read_path(file_path, current_user, db)
    text = _read_text_file(file_path)
    inner = _text_inner_ext(file_path)
    _record_usage_event(
        request, "book_open",
        user=current_user,
        hash_id=_resolve_vault_hash(file_path),
        path=path,
    )
    if inner == ".txt":
        return _convert_txt(text)
    elif inner in CODE_EXTENSIONS:
        return _convert_code(text, inner[1:])
    return _convert_md(text)


@app.get("/api/text-preview")
async def text_preview(
    path: str,
    max_chars: int = 2000,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Return up to max_chars of text from a .md/.txt file for use as a
    cover-slot placeholder preview. For .md the snippet is also rendered to
    HTML so the preview can mirror the in-viewer formatting. Clamp the limit
    to keep responses tiny."""
    file_path = sanitize_text_path(path)
    assert_can_read_path(file_path, current_user, db)
    text = _read_text_file(file_path)
    limit = max(200, min(int(max_chars), 8000))
    snippet = text[:limit]
    html = ""
    inner = _text_inner_ext(file_path)
    if inner != ".txt":
        if inner in CODE_EXTENSIONS:
            html = _render_code_snippet_html(snippet, inner[1:])
        else:
            html = _MdRenderer(collect_toc=False).render(snippet)
    return {
        "text": snippet,
        "html": html,
        "truncated": len(text) > len(snippet),
    }


# ---------------- HTML viewer ----------------

def sanitize_html_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR, exists, and is an .html/.htm/.html.zip file."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    if not (lower.endswith(".html") or lower.endswith(".htm")
            or lower.endswith(".html.zip") or lower.endswith(".htm.zip")):
        raise HTTPException(status_code=400, detail="Not an HTML file")
    return target_path


def _read_html_bytes(file_path: str) -> bytes:
    if file_path.lower().endswith(".zip"):
        with zipfile.ZipFile(file_path) as zf:
            html_entries = [n for n in zf.namelist()
                            if n.lower().endswith((".html", ".htm"))
                            and not n.endswith("/")]
            if not html_entries:
                raise HTTPException(status_code=422, detail="No .html entry inside zip")
            # Prefer the shortest path (typically the document root, not assets/x.html).
            html_entries.sort(key=len)
            return zf.read(html_entries[0])
    with open(file_path, "rb") as f:
        return f.read()


_HTML_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset\s*=\s*["\']?\s*([A-Za-z0-9_\-]+)', re.IGNORECASE
)


def _decode_html_bytes(data: bytes) -> str:
    """Decode HTML bytes using a charset declared via <meta charset>, falling
    back to utf-8 then latin-1. Only the first ~2KB is scanned for the meta
    tag, matching how browsers sniff."""
    m = _HTML_CHARSET_RE.search(data[:2048])
    if m:
        try:
            return data.decode(m.group(1).decode("ascii", errors="replace"))
        except (LookupError, UnicodeDecodeError):
            pass
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_HTML_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

_HTML_DROP_TAGS = {
    "script", "style", "iframe", "object", "embed", "form", "input", "button",
    "select", "textarea", "link", "meta", "svg", "math", "frame", "frameset",
    "applet", "noscript",
}

_HTML_UNWRAP_TAGS = {"html", "body"}

_HTML_ALLOWED_TAGS = {
    "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo",
    "blockquote", "br", "caption", "cite", "code", "col", "colgroup",
    "dd", "del", "details", "dfn", "div", "dl", "dt", "em", "figcaption",
    "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "i", "img", "ins", "kbd", "li", "main", "mark", "nav", "ol",
    "p", "pre", "q", "rp", "rt", "ruby", "s", "samp", "section", "small",
    "span", "strong", "sub", "summary", "sup", "table", "tbody", "td",
    "tfoot", "th", "thead", "time", "tr", "u", "ul", "var", "wbr",
}

_HTML_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"start", "reversed", "type"},
    "li": {"value"},
    "time": {"datetime"},
    "q": {"cite"},
    "blockquote": {"cite"},
}

_HTML_ANCHORED_TAGS = {"p", "div", "section", "article", "pre", "blockquote",
                       "h1", "h2", "h3", "h4", "h5", "h6"}
_HTML_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

_HTML_SAFE_HREF_RE = re.compile(
    r"^(https?:|mailto:|tel:|#|/|\.\.?/)", re.IGNORECASE
)
_HTML_SAFE_IMG_RE = re.compile(
    r"^(https?:|data:image/|/|\.\.?/)", re.IGNORECASE
)


import html.parser as _hp  # avoid clashing with the html.escape import at top


class _HtmlSanitizer(_hp.HTMLParser):
    """Streaming HTML sanitizer that drops scripts/styles/forms, strips event
    handlers and dangerous URLs, anchors block elements for progress tracking,
    and collects a flat TOC from h1–h6."""

    def __init__(self, collect_toc: bool = True, max_chars: int | None = None):
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.collect_toc = collect_toc
        self.toc_flat: List[Dict[str, Any]] = []
        self.title: str = ""
        self.anchor: int = 0
        self._suppress_depth = 0
        self._head_depth = 0
        self._title_depth = 0
        self._heading_stack: List[tuple] = []  # (level, anchor, [text parts])
        self._max_chars = max_chars
        self.truncated = False
        self._len = 0

    def _emit(self, s: str) -> None:
        if self._max_chars is not None and self._len >= self._max_chars:
            self.truncated = True
            return
        self.out.append(s)
        self._len += len(s)

    def _format_attrs(self, tag: str, attrs):
        allowed = _HTML_ALLOWED_ATTRS.get(tag, set())
        parts = []
        for k, v in attrs:
            k = (k or "").lower()
            if not k or k.startswith("on") or k not in allowed:
                continue
            if v is None:
                parts.append(f" {k}")
                continue
            v = v.strip()
            if tag == "a" and k == "href":
                if not _HTML_SAFE_HREF_RE.match(v):
                    v = "#"
            elif tag == "img" and k == "src":
                if not _HTML_SAFE_IMG_RE.match(v):
                    continue
            parts.append(f' {k}="{_html_escape(v, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "head":
            self._head_depth += 1
            self._suppress_depth += 1
            return
        if self._head_depth > 0:
            if tag == "title":
                self._title_depth += 1
            return
        if tag in _HTML_DROP_TAGS:
            self._suppress_depth += 1
            return
        if self._suppress_depth:
            return
        if tag in _HTML_UNWRAP_TAGS or tag not in _HTML_ALLOWED_TAGS:
            return
        attr_str = self._format_attrs(tag, attrs)
        if tag in _HTML_ANCHORED_TAGS:
            n = self.anchor
            self.anchor += 1
            attr_str = f' id="md-a-{n}" data-anchor="{n}"' + attr_str
            if tag in _HTML_HEADING_TAGS:
                self._heading_stack.append((int(tag[1]), n, []))
        slash = "/" if tag in _HTML_VOID_TAGS else ""
        self._emit(f"<{tag}{attr_str}{slash}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "head":
            self._head_depth = max(0, self._head_depth - 1)
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._head_depth > 0:
            if tag == "title":
                self._title_depth = max(0, self._title_depth - 1)
            return
        if tag in _HTML_DROP_TAGS:
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._suppress_depth:
            return
        if tag in _HTML_UNWRAP_TAGS or tag not in _HTML_ALLOWED_TAGS:
            return
        if tag in _HTML_HEADING_TAGS and self._heading_stack:
            level, n, buf = self._heading_stack.pop()
            text = "".join(buf).strip()
            if self.collect_toc and text:
                self.toc_flat.append({"title": text, "level": level, "anchor": n})
        if tag in _HTML_VOID_TAGS:
            return
        self._emit(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        # XHTML-style self-closing. Treat as a start tag; void tags already
        # render as self-closing, non-void inputs were authored as a single
        # element so we still don't want a separate end emit.
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._title_depth > 0 and not self.title:
            t = data.strip()
            if t:
                self.title = t
            return
        if self._head_depth > 0 or self._suppress_depth:
            return
        if self._heading_stack:
            self._heading_stack[-1][2].append(data)
        self._emit(_html_escape(data, quote=False))


def _convert_html(html_bytes: bytes, max_chars: int | None = None) -> Dict[str, Any]:
    raw = _decode_html_bytes(html_bytes)
    sanitizer = _HtmlSanitizer(collect_toc=True, max_chars=max_chars)
    sanitizer.feed(raw)
    sanitizer.close()
    title = sanitizer.title
    if not title:
        for item in sanitizer.toc_flat:
            if item["level"] == 1 and item["title"].strip():
                title = item["title"].strip()
                break
    return {
        "title": title,
        "html": "".join(sanitizer.out),
        "raw": raw,
        "toc": _nest_toc(sanitizer.toc_flat),
        "anchor_count": sanitizer.anchor,
        "truncated": sanitizer.truncated,
    }


@app.get("/api/html-content")
async def html_content(
    request: Request,
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_html_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        data = _read_html_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    _record_usage_event(
        request, "book_open",
        user=current_user,
        hash_id=_resolve_vault_hash(file_path),
        path=path,
    )
    return _convert_html(data)


@app.get("/api/html-preview")
async def html_preview(
    path: str,
    max_chars: int = 2000,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Sanitized HTML snippet for the ItemView cover-slot placeholder. The
    sanitizer stops appending after max_chars of output, so the response stays
    small even for large books."""
    file_path = sanitize_html_path(path)
    assert_can_read_path(file_path, current_user, db)
    limit = max(200, min(int(max_chars), 8000))
    try:
        data = _read_html_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    result = _convert_html(data, max_chars=limit)
    return {
        "title": result["title"],
        "html": result["html"],
        "truncated": result["truncated"],
    }


def sanitize_djvu_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR and exists."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    if not target_path.lower().endswith(".djvu"):
        raise HTTPException(status_code=400, detail="Not a DjVu file")
    return target_path


def _parse_djvu_bookmark(node) -> dict:
    """Convert one djvu sexpr bookmark node into a TOC entry.

    A node is a native tuple (title, link, *children); `link` is "#<page>"
    where a numeric page is 1-based (matching doc.pages[page-1])."""
    title = str(node[0]) if len(node) > 0 else ""
    link = node[1] if len(node) > 1 else ""
    page = None
    if isinstance(link, str) and link.startswith("#") and link[1:].isdigit():
        page = int(link[1:])
    children = [_parse_djvu_bookmark(c) for c in node[2:]]
    return {"title": title, "page": page, "children": children}


def extract_djvu_outline(file_path: str) -> list:
    """Extract the embedded outline (bookmarks) of a DjVu file as a nested
    list of {title, page, children}. Returns [] when the file has none."""
    ctx = djvu.decode.Context()
    doc = ctx.new_document(djvu.decode.FileURI(file_path))
    doc.decoding_job.wait()
    outline = doc.outline
    outline.wait()
    items = list(outline.sexpr)  # [] when the file has no outline
    # items[0] is Symbol('bookmarks'); the rest are bookmark entries.
    return [_parse_djvu_bookmark(e.value) for e in items[1:]]


@app.get("/api/djvu-metadata")
async def djvu_metadata(
    request: Request,
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_djvu_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()
        total_pages = len(doc.pages)
        # Treat djvu-metadata as the book-open signal (one event per open);
        # /api/djvu-page is hit per-page-render and would flood the table.
        _record_usage_event(
            request, "book_open",
            user=current_user,
            hash_id=_resolve_vault_hash(file_path),
            path=path,
        )
        return {"total_pages": total_pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/djvu-outline")
async def djvu_outline(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_djvu_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        return {"toc": extract_djvu_outline(file_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/djvu-page")
async def djvu_page(
    path: str,
    page: int,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_djvu_path(path)
    assert_can_read_path(file_path, current_user, db)
    if page < 1:
        raise HTTPException(status_code=400, detail="Invalid page number")

    headers = {"Cache-Control": "public, max-age=86400"}

    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()

        if page > len(doc.pages):
            raise HTTPException(status_code=404, detail="Page not found")

        djvu_page = doc.pages[page - 1]
        job = djvu_page.decode(wait=True)

        width, height = job.width, job.height
        rect = (0, 0, width, height)
        format = djvu.decode.PixelFormatRgb()
        format.rows_top_to_bottom = True

        try:
            pixels = job.render(djvu.decode.RENDER_COLOR, rect, rect, format)
            img = Image.frombuffer('RGB', (width, height), pixels, 'raw', 'RGB', 0, 1)
        except djvu.decode.NotAvailable:
            img = Image.new('RGB', (width, height), (255, 255, 255))

        output_buffer = io.BytesIO()
        img.save(output_buffer, format="JPEG", quality=85)

        return Response(content=output_buffer.getvalue(), media_type="image/jpeg", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/covers/{hash_id}")
async def get_cover(
    hash_id: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    cover_path = os.path.join(BOOKS_DIR, ".data", "covers", f"{hash_id}.jpg")
    if not os.path.exists(cover_path) or not os.path.isfile(cover_path):
        raise HTTPException(status_code=404, detail="Cover not found")
    if not _is_admin(current_user):
        required = _book_clearance(hash_id, db)
        if required > _clearance_of(current_user):
            raise HTTPException(status_code=403, detail="Forbidden")
    return FileResponse(cover_path)

@app.get("/api/favorites")
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

@app.post("/api/favorites", response_model=schemas.FavoriteResponse)
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

@app.delete("/api/favorites/{hash_id}")
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
    actually be a directory. Returns the canonical relative path (forward
    slashes, no trailing slash)."""
    if path is None:
        raise HTTPException(status_code=400, detail="Invalid path")
    rel = path.strip().lstrip("/").rstrip("/")
    target = os.path.abspath(os.path.join(BOOKS_DIR, rel))
    if not target.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="Directory not found")
    return rel


@app.get("/api/dir-favorites")
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
        full = os.path.abspath(os.path.join(BOOKS_DIR, row.path))
        items.append({
            "id": row.id,
            "path": row.path,
            "name": os.path.basename(row.path) or row.path,
            "exists": full.startswith(os.path.abspath(BOOKS_DIR)) and os.path.isdir(full),
        })
    return {"items": items}


@app.post("/api/dir-favorites", response_model=schemas.DirectoryFavoriteResponse)
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


@app.delete("/api/dir-favorites")
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
            cover_exists[bid] = os.path.exists(os.path.join(DATA_DIR, "covers", f"{bid}.jpg"))

    out: list[dict] = []
    for it in items:
        if it.item_type == "directory":
            path = it.dir_path or ""
            if not owner_view and not _dir_item_visible(db, path, user):
                continue
            full = os.path.abspath(os.path.join(BOOKS_DIR, path))
            exists = full.startswith(os.path.abspath(BOOKS_DIR)) and os.path.isdir(full)
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
            cover_exists[bid] = os.path.exists(os.path.join(DATA_DIR, "covers", f"{bid}.jpg"))

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


@app.get("/api/playlists")
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
                cover_exists[bid] = os.path.exists(os.path.join(DATA_DIR, "covers", f"{bid}.jpg"))
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


@app.get("/api/playlists/contained-keys")
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


@app.get("/api/playlists/membership")
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


@app.post("/api/playlists")
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


@app.get("/api/playlists/{playlist_id}")
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


@app.patch("/api/playlists/{playlist_id}")
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


@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, request: Request, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    pl = _get_owned_playlist(db, playlist_id, current_user)
    if pl.kind == "bookshelf":
        raise HTTPException(status_code=403, detail="Forbidden")
    pl_name = pl.name
    db.query(models.PlaylistItem).filter(models.PlaylistItem.playlist_id == pl.id).delete()
    db.delete(pl)
    db.commit()
    _record_usage_event(request, "playlist_delete", user=current_user,
                        path=f"playlists/{playlist_id}",
                        extra={"playlist_id": playlist_id, "name": pl_name})
    return {"message": "Deleted"}


@app.post("/api/playlists/{playlist_id}/items")
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


@app.delete("/api/playlists/{playlist_id}/items/{item_id}")
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


@app.delete("/api/playlists/{playlist_id}/items")
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


@app.put("/api/playlists/{playlist_id}/order")
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


@app.post("/api/playlists/{playlist_id}/share")
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
        _record_usage_event(request, "playlist_visibility", user=current_user,
                            path=f"playlists/{pl.id}",
                            extra={"playlist_id": pl.id, "name": pl.name, "visibility": "public"})
    return {"token": pl.share_token, "visibility": pl.visibility}


@app.delete("/api/playlists/{playlist_id}/share")
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


@app.get("/api/shared/{token}")
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


@app.post("/api/shared/{token}/copy")
async def copy_shared_playlist(token: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
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
    return {"id": copy.id, "item_count": pos}


@app.get("/api/progress/{hash_id}", response_model=schemas.ReadingProgressResponse)
async def get_progress(hash_id: str, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if current_user is None:  # guest — no saved progress
        raise HTTPException(status_code=404, detail="No progress found")
    if not _is_admin(current_user) and _book_clearance(hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    progress = db.query(models.ReadingProgress).filter(
        models.ReadingProgress.user_id == current_user.id,
        models.ReadingProgress.hash_id == hash_id
    ).first()
    if not progress:
        raise HTTPException(status_code=404, detail="No progress found")
    return progress

@app.post("/api/progress", response_model=schemas.ReadingProgressResponse)
async def update_progress(prog: schemas.ReadingProgressCreate, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    pct = prog.percent
    if pct is not None:
        pct = max(0.0, min(1.0, float(pct)))
    if current_user is None:  # guest — accept silently, nothing is persisted
        return {"id": 0, "user_id": 0, "hash_id": prog.hash_id, "location": prog.location, "percent": pct}
    if not _is_admin(current_user) and _book_clearance(prog.hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    existing = db.query(models.ReadingProgress).filter(
        models.ReadingProgress.user_id == current_user.id,
        models.ReadingProgress.hash_id == prog.hash_id
    ).first()
    if existing:
        existing.location = prog.location
        if pct is not None:
            existing.percent = pct
        db.commit()
        db.refresh(existing)
        return existing

    new_prog = models.ReadingProgress(user_id=current_user.id, hash_id=prog.hash_id, location=prog.location, percent=pct)
    db.add(new_prog)
    db.commit()
    db.refresh(new_prog)
    return new_prog

# ==============================================================================
# Ratings
# ==============================================================================
# A 1-5 star rating, one per user per book. Not moderated — it counts toward the
# book's average as soon as it is submitted.

@app.get("/api/books/{hash_id}/rating", response_model=schemas.RatingResponse)
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


@app.post("/api/books/{hash_id}/rating", response_model=schemas.RatingResponse)
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


@app.delete("/api/books/{hash_id}/rating")
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


def _author_name(email: str, real_name: str | None = None) -> str:
    """Public display name for a commenter — their chosen real name if they set
    one, otherwise the local-part of their email (so we never expose the full
    address to other users)."""
    if real_name and real_name.strip():
        return real_name.strip()
    return (email or "user").split("@", 1)[0]


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


@app.get("/api/books/{hash_id}/comments")
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


@app.post("/api/books/{hash_id}/comments")
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


@app.put("/api/comments/{comment_id}")
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


@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if not row:
        return {"message": "Removed"}
    if row.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    # No DB cascade is enforced — drop replies of a top-level comment by hand.
    if row.parent_id is None:
        db.query(models.BookComment).filter(models.BookComment.parent_id == row.id).delete()
    db.delete(row)
    db.commit()
    return {"message": "Removed"}


# ---------- Admin: comment moderation ----------

@app.get("/api/admin/comments")
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


@app.post("/api/admin/comments/{comment_id}/approve")
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


@app.delete("/api/admin/comments/{comment_id}")
async def admin_delete_comment(comment_id: int, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if row:
        author_id = row.user_id
        book_hash = row.hash_id
        author_email = db.query(models.User.email).filter(models.User.id == author_id).scalar()
        if row.parent_id is None:
            db.query(models.BookComment).filter(models.BookComment.parent_id == row.id).delete()
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


@app.get("/api/books/{hash_id}/annotations")
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


@app.post("/api/annotations")
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


@app.put("/api/annotations/{annotation_id}")
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


@app.delete("/api/annotations/{annotation_id}")
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

@app.get("/api/admin/annotations")
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


@app.post("/api/admin/annotations/{annotation_id}/approve")
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


@app.delete("/api/admin/annotations/{annotation_id}")
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


# ==============================================================================
# Librarian audit log
# ==============================================================================
# Read-only views over admin_audit_log. Writing happens via _audit() at the
# call sites; here we just paginate the feed and aggregate the leaderboard.
# Both endpoints require admin (the log lets you see what other admins did,
# so it inherently must not leak to non-admins).

def _audit_actor_dict(user: models.User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "real_name": user.real_name,
        "avatar_url": user.avatar_url,
    }


@app.get("/api/admin/audit", response_model=schemas.AuditFeed)
async def admin_audit_feed(
    page: int = 1,
    per_page: int | None = None,
    actor_user_id: int | None = None,
    action: str | None = None,
    since: str | None = None,            # ISO-8601 UTC; only entries with created_at >= this are returned
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Page-based timeline of admin actions, newest first. Symmetric with
    `/api/admin/usage/timeline` and `/api/me/activity` — Prev / Page X of N /
    Next is friendlier than scrolling through 1000+ rows under "Load more".

    `per_page` defaults to the admin's own `users.search_per_page` setting
    (same knob as Search), with a 50-row fallback when unset."""
    if per_page is None:
        per_page = int(_admin.search_per_page or 50)
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))
    # Outer join because the actor row may have been hard-deleted (FKs are
    # not enforced in this SQLite). Matches the 'actor user was deleted'
    # tolerance already in admin_audit_stats — without this, deleting a
    # user account would silently drop their entire history from the feed.
    q = (
        db.query(models.AdminAuditLog, models.User)
          .outerjoin(models.User, models.AdminAuditLog.actor_user_id == models.User.id)
          .order_by(models.AdminAuditLog.id.desc())
    )
    if actor_user_id is not None:
        q = q.filter(models.AdminAuditLog.actor_user_id == actor_user_id)
    if action:
        q = q.filter(models.AdminAuditLog.action == action)
    if since:
        q = q.filter(models.AdminAuditLog.created_at >= since)

    total = q.order_by(None).count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()

    entries = []
    for entry, user in rows:
        details = None
        if entry.details_json:
            try:
                details = json.loads(entry.details_json)
            except json.JSONDecodeError:
                details = {"_raw": entry.details_json}  # don't crash the feed over a malformed row
        if user is None:
            # Actor was hard-deleted. Synthesize a placeholder so the row
            # stays visible — the audit log's append-only contract takes
            # precedence over hiding orphans.
            actor = {
                "id": entry.actor_user_id,
                "email": "",
                "real_name": "(deleted user)",
                "avatar_url": None,
            }
        else:
            actor = _audit_actor_dict(user)
        entries.append({
            "id": entry.id,
            "created_at": entry.created_at,
            "actor": actor,
            "action": entry.action,
            "target_kind": entry.target_kind,
            "target_id": entry.target_id,
            "summary": entry.summary,
            "details": details,
        })

    return {
        "entries": entries,
        "page": page,
        "per_page": per_page,
        "total": int(total),
        "total_pages": (int(total) + per_page - 1) // per_page,
    }


@app.get("/api/admin/audit/stats", response_model=schemas.AuditStats)
async def admin_audit_stats(
    since: str | None = None,            # ISO-8601 UTC; default = 30 days ago
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-admin tallies grouped by action, for the leaderboard view. One
    GROUP BY query plus a single users lookup; cheap even on a busy log."""
    if since is None:
        # Match the format _now_iso() writes into admin_audit_log.created_at
        # ('YYYY-MM-DDTHH:MM:SSZ') so the lexicographic >= comparison agrees
        # with chronological order. datetime.isoformat() would produce a
        # '...+00:00' suffix that sorts BEFORE 'Z' at the boundary second.
        since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = (
        db.query(
            models.AdminAuditLog.actor_user_id,
            models.AdminAuditLog.action,
            func.count().label("n"),
        )
        .filter(models.AdminAuditLog.created_at >= since)
        .group_by(models.AdminAuditLog.actor_user_id, models.AdminAuditLog.action)
        .all()
    )
    by_actor: dict[int, dict[str, int]] = {}
    for actor_id, act, n in rows:
        by_actor.setdefault(actor_id, {})[act] = n

    users = (
        db.query(models.User)
          .filter(models.User.id.in_(by_actor.keys()))
          .all()
        if by_actor else []
    )
    user_by_id = {u.id: u for u in users}

    leaders = []
    for actor_id, totals in by_actor.items():
        u = user_by_id.get(actor_id)
        if not u:
            continue  # actor user was deleted; skip the row (FKs aren't enforced in this SQLite)
        leaders.append({
            "actor": _audit_actor_dict(u),
            "totals": totals,
            "total": sum(totals.values()),
        })
    leaders.sort(key=lambda r: r["total"], reverse=True)

    return {"since": since, "leaders": leaders}


# ==============================================================================
# Admin → Usage analytics (reads from usage_events)
# ==============================================================================
# All endpoints below are admin-only. The shape mirrors /api/admin/audit/* —
# `days` is a relative window (default 30), and the response is a plain dict
# (no response_model) so the surface stays easy to evolve. Queries are
# indexed via ix_usage_events_*: ts / (user_id,ts) / (ip,ts) / (hash_id,ts)
# / (kind,ts).

# Recording kill-switch. Admin can toggle which event kinds get inserted from
# /api/admin/usage/settings. Module-level set is replaced (not mutated)
# whenever the admin saves, so readers in _record_usage_event need no lock —
# CPython's GIL guarantees an atomic name rebind, and `in` on a set is O(1).
_USAGE_KINDS_ALL = ("page", "book_open", "search", "login", "register",
                    "recommend", "unrecommend",
                    "playlist_create", "playlist_delete",
                    "playlist_add_item", "playlist_remove_item",
                    "playlist_visibility")
_enabled_kinds: frozenset[str] = frozenset(_USAGE_KINDS_ALL)


def _load_enabled_kinds() -> None:
    """Read the persisted enabled-kinds set from app_meta into the module
    cache. Called once at startup and after each admin update. Falls back to
    'all enabled' if the row is missing or malformed (the safer default —
    matches the Privacy Policy's published list)."""
    global _enabled_kinds
    db = SessionLocal()
    try:
        row = db.query(models.AppMeta).filter(
            models.AppMeta.key == "usage_events_enabled_kinds"
        ).first()
        if row and row.value:
            try:
                vals = json.loads(row.value)
                if isinstance(vals, list):
                    _enabled_kinds = frozenset(v for v in vals if v in _USAGE_KINDS_ALL)
                    return
            except json.JSONDecodeError:
                pass
        _enabled_kinds = frozenset(_USAGE_KINDS_ALL)
    finally:
        db.close()


_load_enabled_kinds()


def _usage_since(days: int | None) -> str:
    """Return an ISO-8601 UTC timestamp `days` ago, matching the format
    written into usage_events.ts so lexicographic >= agrees with chronology."""
    d = max(1, min(int(days) if days else 30, 3650))
    return (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.get("/api/admin/usage/overview")
async def admin_usage_overview(
    days: int = 30,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """High-level rollup for the Admin → Usage landing page: totals by kind,
    distinct users / IPs / countries seen, and a per-day count for a sparkline."""
    since = _usage_since(days)
    base = db.query(models.UsageEvent).filter(models.UsageEvent.ts >= since)

    by_kind_rows = (
        db.query(models.UsageEvent.kind, func.count())
          .filter(models.UsageEvent.ts >= since)
          .group_by(models.UsageEvent.kind)
          .all()
    )
    by_kind = {k: int(n) for k, n in by_kind_rows}

    total_events = base.count()
    unique_users = (
        db.query(func.count(func.distinct(models.UsageEvent.user_id)))
          .filter(models.UsageEvent.ts >= since)
          .filter(models.UsageEvent.user_id.isnot(None))
          .scalar() or 0
    )
    unique_ips = (
        db.query(func.count(func.distinct(models.UsageEvent.ip)))
          .filter(models.UsageEvent.ts >= since)
          .scalar() or 0
    )
    unique_countries = (
        db.query(func.count(func.distinct(models.UsageEvent.geo_country)))
          .filter(models.UsageEvent.ts >= since)
          .filter(models.UsageEvent.geo_country.isnot(None))
          .scalar() or 0
    )

    # Daily sparkline. substr(ts,1,10) extracts the YYYY-MM-DD prefix.
    daily_rows = (
        db.query(func.substr(models.UsageEvent.ts, 1, 10), func.count())
          .filter(models.UsageEvent.ts >= since)
          .group_by(func.substr(models.UsageEvent.ts, 1, 10))
          .order_by(func.substr(models.UsageEvent.ts, 1, 10))
          .all()
    )
    daily = [{"date": d, "count": int(n)} for d, n in daily_rows]

    return {
        "since": since,
        "days": days,
        "total_events": int(total_events),
        "by_kind": by_kind,
        "unique_users": int(unique_users),
        "unique_ips": int(unique_ips),
        "unique_countries": int(unique_countries),
        "daily": daily,
    }


@app.get("/api/admin/usage/by-country")
async def admin_usage_by_country(
    days: int = 30,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Country leaderboard: events / unique IPs / unique users per ISO-2."""
    since = _usage_since(days)
    rows = (
        db.query(
            models.UsageEvent.geo_country,
            func.count().label("n"),
            func.count(func.distinct(models.UsageEvent.ip)).label("ips"),
            func.count(func.distinct(models.UsageEvent.user_id)).label("users"),
        )
        .filter(models.UsageEvent.ts >= since)
        .group_by(models.UsageEvent.geo_country)
        .order_by(func.count().desc())
        .all()
    )
    return {
        "since": since,
        "days": days,
        "countries": [
            {"country": c or "UNKNOWN", "events": int(n), "unique_ips": int(ips), "unique_users": int(users)}
            for c, n, ips, users in rows
        ],
    }


@app.get("/api/admin/usage/by-city")
async def admin_usage_by_city(
    country: str,
    days: int = 30,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Cities within a given ISO-2 country code, by event count."""
    since = _usage_since(days)
    rows = (
        db.query(
            models.UsageEvent.geo_city,
            func.count().label("n"),
            func.count(func.distinct(models.UsageEvent.ip)).label("ips"),
        )
        .filter(models.UsageEvent.ts >= since)
        .filter(models.UsageEvent.geo_country == country)
        .group_by(models.UsageEvent.geo_city)
        .order_by(func.count().desc())
        .all()
    )
    return {
        "since": since,
        "days": days,
        "country": country,
        "cities": [
            {"city": c or "UNKNOWN", "events": int(n), "unique_ips": int(ips)}
            for c, n, ips in rows
        ],
    }


@app.get("/api/admin/usage/by-book")
async def admin_usage_by_book(
    days: int = 30,
    limit: int = 50,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Most-opened books in the window. Joined to books for the title/author;
    a NULL join row means the book was deleted (ON DELETE SET NULL keeps the
    event but drops the hash_id), so those rows are filtered out.

    `signed_in_readers` counts distinct user_id values (NULLs ignored by SQL),
    while `guest_ips` counts distinct ip values from rows where user_id IS NULL.
    Splitting them avoids the misleading 'opened by 0 readers' that you'd see
    when the book was only opened by guests."""
    since = _usage_since(days)
    limit = max(1, min(int(limit), 500))
    rows = (
        db.query(
            models.UsageEvent.hash_id,
            models.Book.title,
            models.Book.author,
            func.count().label("opens"),
            func.count(func.distinct(models.UsageEvent.user_id)).label("signed_in_readers"),
            func.count(func.distinct(case(
                (models.UsageEvent.user_id.is_(None), models.UsageEvent.ip),
                else_=None,
            ))).label("guest_ips"),
        )
        .outerjoin(models.Book, models.Book.id == models.UsageEvent.hash_id)
        .filter(models.UsageEvent.ts >= since)
        .filter(models.UsageEvent.kind == "book_open")
        .filter(models.UsageEvent.hash_id.isnot(None))
        .group_by(models.UsageEvent.hash_id)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )
    return {
        "since": since,
        "days": days,
        "books": [
            {
                "hash_id": h,
                "title": title,
                "author": author,
                "opens": int(opens),
                "signed_in_readers": int(readers),
                "guest_ips": int(guests),
            }
            for h, title, author, opens, readers, guests in rows
        ],
    }


@app.get("/api/admin/usage/by-user")
async def admin_usage_by_user(
    days: int = 30,
    limit: int = 100,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-user activity rollup. Guest rows (user_id IS NULL) are excluded;
    those are surfaced via /by-ip instead."""
    since = _usage_since(days)
    limit = max(1, min(int(limit), 1000))
    rows = (
        db.query(
            models.UsageEvent.user_id,
            models.User.email,
            models.User.real_name,
            func.count().label("total"),
            func.count(func.distinct(case((models.UsageEvent.kind == "book_open", models.UsageEvent.hash_id), else_=None))).label("books"),
            func.max(models.UsageEvent.ts).label("last_seen"),
        )
        .join(models.User, models.User.id == models.UsageEvent.user_id)
        .filter(models.UsageEvent.ts >= since)
        .group_by(models.UsageEvent.user_id)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )
    return {
        "since": since,
        "days": days,
        "users": [
            {
                "user_id": uid,
                "email": email,
                "real_name": real_name,
                "total_events": int(total),
                "unique_books_opened": int(books),
                "last_seen": last_seen,
            }
            for uid, email, real_name, total, books, last_seen in rows
        ],
    }


@app.get("/api/admin/usage/by-ip")
async def admin_usage_by_ip(
    days: int = 30,
    country: str | None = None,
    limit: int = 100,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-IP rollup, optionally filtered to a single country. One row per IP
    with first_seen / last_seen extremes."""
    since = _usage_since(days)
    limit = max(1, min(int(limit), 1000))
    q = (
        db.query(
            models.UsageEvent.ip,
            models.UsageEvent.geo_country,
            models.UsageEvent.geo_city,
            func.count().label("n"),
            func.count(func.distinct(models.UsageEvent.user_id)).label("users"),
            func.min(models.UsageEvent.ts).label("first_seen"),
            func.max(models.UsageEvent.ts).label("last_seen"),
        )
        .filter(models.UsageEvent.ts >= since)
    )
    if country:
        q = q.filter(models.UsageEvent.geo_country == country)
    rows = (
        q.group_by(models.UsageEvent.ip)
         .order_by(func.count().desc())
         .limit(limit)
         .all()
    )
    return {
        "since": since,
        "days": days,
        "country": country,
        "ips": [
            {
                "ip": ip,
                "country": c,
                "city": city,
                "events": int(n),
                "unique_users": int(users),
                "first_seen": first_seen,
                "last_seen": last_seen,
            }
            for ip, c, city, n, users, first_seen, last_seen in rows
        ],
    }


@app.get("/api/admin/usage/settings")
async def admin_usage_settings_get(
    _admin: models.User = Depends(require_admin),
):
    """Return the current set of usage_event kinds being recorded, and the
    full list of recognised kinds so the UI can render every checkbox even
    if a future kind is added in code but not yet enabled."""
    return {
        "enabled_kinds": sorted(_enabled_kinds),
        "all_kinds": list(_USAGE_KINDS_ALL),
    }


@app.put("/api/admin/usage/settings")
async def admin_usage_settings_put(
    payload: schemas.UsageSettingsUpdate,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Replace the enabled-kinds set. Silently drops unknown kind names —
    the persisted set is always a subset of _USAGE_KINDS_ALL."""
    new_set = frozenset(v for v in payload.enabled_kinds if v in _USAGE_KINDS_ALL)
    payload_json = json.dumps(sorted(new_set))

    row = db.query(models.AppMeta).filter(
        models.AppMeta.key == "usage_events_enabled_kinds"
    ).first()
    old_value = row.value if row else None
    if row:
        row.value = payload_json
    else:
        db.add(models.AppMeta(key="usage_events_enabled_kinds", value=payload_json))

    _audit(
        db, _admin, "usage.kinds_update",
        target_kind="settings",
        target_id="usage_events_enabled_kinds",
        summary=f"Usage recording: {', '.join(sorted(new_set)) or '(none)'}",
        details={"old": old_value, "new": payload_json},
    )
    db.commit()

    _load_enabled_kinds()
    return {
        "enabled_kinds": sorted(_enabled_kinds),
        "all_kinds": list(_USAGE_KINDS_ALL),
    }


@app.get("/api/admin/usage/timeline")
async def admin_usage_timeline(
    page: int = 1,
    per_page: int | None = None,
    user: str | None = None,           # accepts numeric user_id or full email; admin disambiguates
    ip: str | None = None,
    hash_id: str | None = None,
    kind: str | None = None,
    country: str | None = None,
    since: str | None = None,
    until: str | None = None,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Raw event feed, newest first, page-based pagination (the matching
    Audit endpoint still uses cursor + 'Load more' — we use page-numbers here
    for symmetry with #/account/activity, which the operator preferred).

    All filters compose with AND. `per_page` defaults to the admin's own
    `users.search_per_page` setting (same knob as Search) so pagination is
    consistent across all admin tables.

    `user` accepts either a numeric id (matches usage_events.user_id) or an
    email address (case-insensitive exact match against users.email). A
    nonexistent email yields an empty result, not a 404."""
    if per_page is None:
        per_page = int(_admin.search_per_page or 100)
    page = max(page, 1)
    per_page = max(1, min(per_page, 500))
    q = (
        db.query(models.UsageEvent, models.User)
          .outerjoin(models.User, models.User.id == models.UsageEvent.user_id)
          .order_by(models.UsageEvent.id.desc())
    )
    if user:
        token = user.strip()
        token_lc = token.lower()
        if token_lc in ("guest", "(guest)"):
            q = q.filter(models.UsageEvent.user_id.is_(None))
        elif token.isdigit():
            q = q.filter(models.UsageEvent.user_id == int(token))
        else:
            # Email: resolve once, then filter by the integer user_id so the
            # SQLite indexes on usage_events still apply. A lookup that finds
            # nothing forces a deliberately empty result set rather than
            # filtering by NULL (which would silently match guest rows).
            resolved = db.query(models.User.id).filter(
                func.lower(models.User.email) == token_lc
            ).scalar()
            if resolved is None:
                q = q.filter(models.UsageEvent.id == -1)
            else:
                q = q.filter(models.UsageEvent.user_id == resolved)
    if ip:
        q = q.filter(models.UsageEvent.ip == ip)
    if hash_id:
        q = q.filter(models.UsageEvent.hash_id == hash_id)
    if kind:
        q = q.filter(models.UsageEvent.kind == kind)
    if country:
        q = q.filter(models.UsageEvent.geo_country == country)
    if since:
        q = q.filter(models.UsageEvent.ts >= since)
    if until:
        q = q.filter(models.UsageEvent.ts <= until)

    total = q.count()
    rows = q.offset((page - 1) * per_page).limit(per_page).all()

    entries = []
    for ev, user in rows:
        extra = None
        if ev.extra_json:
            try:
                extra = json.loads(ev.extra_json)
            except json.JSONDecodeError:
                extra = {"_raw": ev.extra_json}
        entries.append({
            "id": ev.id,
            "ts": ev.ts,
            "user_id": ev.user_id,
            "user_email": user.email if user else None,
            "session_jti": ev.session_jti,
            "ip": ev.ip,
            "user_agent": ev.user_agent,
            "geo_country": ev.geo_country,
            "geo_city": ev.geo_city,
            "kind": ev.kind,
            "path": ev.path,
            "hash_id": ev.hash_id,
            "extra": extra,
        })

    return {
        "entries": entries,
        "page": page,
        "per_page": per_page,
        "total": int(total),
        "total_pages": (int(total) + per_page - 1) // per_page,
    }


# ==============================================================================
# Feedback / Contact-admin
# ==============================================================================
# Sibling to the comments system: user→admin tickets with status workflow,
# admin reply (+ internal notes), and a throttled per-recipient digest email.

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

@app.post("/api/feedback")
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


@app.post("/api/feedback/{thread_id}/attachment")
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


@app.get("/api/feedback/{thread_id}/attachment/{filename}")
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


@app.get("/api/feedback")
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


@app.get("/api/feedback/{thread_ref}")
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


@app.post("/api/feedback/{thread_id}/reply")
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

    if kind != "internal":
        if is_admin_actor:
            _maybe_email_user_update(thread, db, change="reply")
            if status_flipped:
                _maybe_email_user_update(thread, db, change="status")
        # User replies don't trigger an admin email — admins see updates on next inbox load.

    return {"ok": True}


@app.post("/api/feedback/{thread_id}/resolve")
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

@app.get("/api/admins")
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


@app.get("/api/admin/feedback")
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


@app.patch("/api/admin/feedback/{thread_id}/status")
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


@app.post("/api/admin/feedback/{thread_id}/assign")
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


@app.delete("/api/admin/feedback/{thread_id}")
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
    # Delete child rows explicitly: this SQLite connection runs with foreign-key
    # enforcement OFF (see database.py), so the models' ondelete="CASCADE" does
    # nothing — relying on it would orphan the messages/recipients/attachments.
    tid = thread.id
    db.query(models.FeedbackMessage).filter(models.FeedbackMessage.thread_id == tid).delete(synchronize_session=False)
    db.query(models.FeedbackRecipient).filter(models.FeedbackRecipient.thread_id == tid).delete(synchronize_session=False)
    db.query(models.FeedbackAttachment).filter(models.FeedbackAttachment.thread_id == tid).delete(synchronize_session=False)
    db.delete(thread)
    db.commit()
    return {"message": "removed"}


@app.get("/api/admin/feedback/settings")
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


@app.put("/api/admin/feedback/settings")
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


@app.post("/api/admin/feedback/digest/force")
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


# ---------- User: notification prefs ----------

@app.get("/api/users/me/notification-prefs")
async def get_my_notification_prefs(
    current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db),
):
    p = _user_notif_prefs(db, current_user.id)
    return {
        "email_on_reply": bool(p.email_on_reply),
        "email_on_status": bool(p.email_on_status),
        "email_weekly_summary": bool(p.email_weekly_summary),
    }


@app.put("/api/users/me/notification-prefs")
async def set_my_notification_prefs(
    payload: schemas.NotificationPrefs,
    current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db),
):
    p = _user_notif_prefs(db, current_user.id)
    p.email_on_reply = payload.email_on_reply
    p.email_on_status = payload.email_on_status
    p.email_weekly_summary = payload.email_weekly_summary
    p.updated_at = _now_iso()
    db.commit()
    return {"ok": True}


# Frontend static files will be mounted here later
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="frontend")
