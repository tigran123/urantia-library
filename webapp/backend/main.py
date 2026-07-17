# main.py is now pure wiring: it builds the FastAPI app, registers middleware +
# the lifespan, runs the module-level startup steps, and mounts the routers and
# the SPA. All routes and business logic live in the foundation modules
# (config/state/cas/deps/serialize/paths/background) and the routers/ package;
# see the re-export block below for the main.<symbol> backward-compat surface.
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import asyncio
import logging
import threading  # noqa: F401 — kept so tests can monkeypatch main.threading.Thread (and main.os)
import jwt
import models
import database  # bound so database.SessionLocal() in _lifespan picks up the test swap
from database import engine, get_db, verify_schema_version  # get_db kept: tests use main.get_db
from security import SECRET_KEY, ALGORITHM

models.Base.metadata.create_all(bind=engine)
verify_schema_version(engine)


# ---------------------------------------------------------------------------
# Foundation modules extracted from this file. Every moved symbol is re-imported
# back into main's namespace so the ~150 route handlers and _lifespan keep
# calling bare names (BOOKS_DIR, get_current_user, _record_usage_event, ...)
# unchanged. See the module docstrings for the split rationale and DAG.
# ---------------------------------------------------------------------------
import config as config
import state as state
import cas as cas
import deps as deps
import serialize as serialize
import paths as paths
import background as background
from config import (
    BOOKS_DIR, DATA_DIR, FEEDBACK_ATTACHMENT_DIR, RECOMMENDED_SUBDIR, RECOMMENDED_DIR,
    _TOPDIR_SKIPLIST, _is_recommended_path, GEOIP_DB_PATH, _geo_lookup, ONLINE_WINDOW,
    _LAST_SEEN_FLUSH_INTERVAL_SECONDS, _now_iso, AVATAR_DIR, STAGING_DIR, COVERS_DIR,
    _STAGING_TTL_S, _MAX_UPLOAD_BYTES, _MAX_COVER_BYTES, _AVATAR_MAX_BYTES,
    _ACCEPTED_BOOK_EXTS, _AUDIO_EXTS, _VIDEO_EXTS, _MULTI_SUFFIXES, _IMAGE_EXTS,
    _detect_format, _effective_suffix, _text_inner_ext, _USAGE_KINDS_ALL,
    _escape_like, CODE_EXTENSIONS, MIN_PASSWORD_LENGTH,
)
from state import (
    _last_seen, _last_seen_lock, _active_sessions, _active_sessions_lock,
    _purge_expired_sessions_locked, _AUDIT_BATCHES, _AUDIT_BATCHES_LOCK,
    _AUDIT_BATCH_TTL_S, _STAGING, _STAGING_LOCK, INTEGRITY_JOBS, INTEGRITY_JOBS_LOCK,
    INTEGRITY_JOB_TTL_SECONDS, INTEGRITY_ALL_RESULTS_CAP,
)
from cas import (
    _resolve_vault_hash, _VERIFY_CHUNK, _blake2b_of_file, _verify_book_sync,
    _staged_reads_as, _zip_fb2_inplace, _run, _extract_upload_metadata,
    _extract_media_meta, _extract_cover_to, _read_text_bytes,
)
from deps import (
    _decode_claims, _current_jti, get_current_user, require_admin, get_optional_user,
    _clearance_of, _is_admin, _ACCESS_TOKEN_LIFETIME, SESSION_REFRESH_INTERVAL,
    _maybe_refresh_session,
)
from serialize import _rating_stats, _attach_recommendations, _author_name
from paths import (
    _resolves_into_infra, _first_book_path, _primary_topic_path, _book_clearance,
    assert_can_read_path, _accessible_locations_query, _subtree_is_unmanaged,
    _safe_under_books, _assert_mutation_path, sanitize_fb2_path,
    sanitize_text_path, sanitize_html_path, sanitize_djvu_path,
)
from background import (
    _send_legal_blast_emails, _maybe_send_legal_blast, _audit, _record_usage_event,
    _persist_session, _delete_session, _delete_user_sessions, _purge_expired_session_rows,
    _load_active_sessions, _flush_last_seen, _flush_last_seen_once, _last_seen_flush_loop,
    _seed_admin_feedback_settings, _purge_expired_staging, _load_enabled_kinds,
    _purge_expired_reset_tokens, _dispatch_reset_request, _process_reset_request,
)


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
    try:
        # Reload persisted JWT sessions so a restart doesn't log everyone out.
        _load_active_sessions()
    except Exception:
        logging.exception("startup: auth session rehydration failed; sessions will re-login")
    try:
        # Housekeeping: drop expired/used password-reset tokens. The reset
        # endpoint already rejects them, so this just keeps the table small.
        _reset_purge_db = database.SessionLocal()
        try:
            _purge_expired_reset_tokens(_reset_purge_db)
        finally:
            _reset_purge_db.close()
    except Exception:
        logging.exception("startup: password reset token sweep failed; will retry on next restart")

    # Periodically mirror the in-memory _last_seen presence map into
    # users.last_seen_at so Admin → Users shows a last-seen time for every user
    # (the hot auth deps stay write-free; see _flush_last_seen). Lost on a hard
    # crash like every other in-process job, but flushed on a clean shutdown.
    flush_task = asyncio.create_task(_last_seen_flush_loop())
    try:
        yield
    finally:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.exception("shutdown: last_seen flush loop raised on cancel")
        try:
            _flush_last_seen_once()
        except Exception:
            logging.exception("shutdown: final last_seen flush failed")

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

    # No timestamp here on purpose: stdout is captured by systemd-journald, which
    # already records the full timestamp (year + tz + microseconds). View it with
    # `journalctl -o short-iso` to surface the year that the default `short` hides.
    url_with_query = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
    print(f"{client_host} {user_email} \"{request.method} {url_with_query} HTTP/{request.scope.get('http_version', '1.1')}\" {response.status_code}")

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


_seed_admin_feedback_settings()


app.mount("/api/avatars", StaticFiles(directory=AVATAR_DIR), name="avatars")

# Module-level startup: hydrate the usage-recording kill-switch set from
# app_meta. The admin usage-settings routes (now in routers/admin_usage.py)
# re-run _load_enabled_kinds() on save.
_load_enabled_kinds()


# ---------------------------------------------------------------------------
# Routers extracted from this file (Wave 1). Each owns a distinct path prefix;
# cross-router order is not significant. Must be included BEFORE the SPA mount
# below (that catch-all mount must stay last).
# ---------------------------------------------------------------------------
from routers import feedback
from routers import notifications
from routers import progress
from routers import admin_integrity
from routers import admin_usage
from routers import social
from routers import search
from routers import admin_users
from routers import playlists
from routers import admin_books
from routers import admin_uploads
from routers import auth
from routers import browse
from routers import content
# A test reads main._FEEDBACK_DIGEST_META_KEY; keep it resolvable from main.
from routers.feedback import _FEEDBACK_DIGEST_META_KEY
# admin_integrity reaches `main._expand_dirs_to_hash_ids` via a call-time shim;
# keep it resolvable from main after the move to routers/admin_books.py.
from routers.admin_books import _expand_dirs_to_hash_ids
# Auth helpers stay reachable as main.X (CLI tools / tests / re-export contract).
from routers.auth import (
    _user_response_dict, _my_activity_row, _delete_avatar_file, _process_avatar,
)
# _list_directory stays reachable as main.X (re-export contract).
from routers.browse import _list_directory
# admin_uploads reaches these content converters via a call-time `main.<fn>`
# shim; keep them resolvable from main after the move to routers/content.py.
from routers.content import (
    _read_fb2_bytes, _convert_fb2, _read_text_file, _convert_txt, _convert_code,
    _convert_md, _read_html_bytes, _convert_html, extract_djvu_outline,
)
app.include_router(feedback.router)
app.include_router(notifications.router)
app.include_router(progress.router)
app.include_router(admin_integrity.router)
app.include_router(admin_usage.router)
app.include_router(social.router)
app.include_router(search.router)
app.include_router(admin_users.router)
app.include_router(playlists.router)
app.include_router(admin_books.router)
app.include_router(admin_uploads.router)
app.include_router(auth.router)
app.include_router(browse.router)
app.include_router(content.router)


# Frontend static files will be mounted here later
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="frontend")
