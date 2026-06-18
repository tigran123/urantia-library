"""Foundation module (extracted from main.py): background / off-request-path
work — the legal-update blast, auth-session persistence + rehydration, the
presence flush loop, audit appends, usage-event recording, staging GC, the
admin-feedback-settings seed, and the usage kill-switch loader. Depends on
state, config, models, database, email_utils, security.

Per the strict DAG, background must NOT import deps or paths. Where a moved
function needs a trivial clearance/admin check it inlines it
(`user.clearance if user else 0` / `bool(user and user.is_admin)`) rather than
importing deps."""
import os
import threading
import asyncio
import logging
import re
import json
import shutil
from datetime import datetime, timezone
import jwt
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from fastapi import Request
import models
import email_utils
from database import SessionLocal, engine, LEGAL_VERSION
from security import SECRET_KEY, ALGORITHM
import state
from state import (
    _active_sessions, _active_sessions_lock, _last_seen, _last_seen_lock,
    _STAGING, _STAGING_LOCK,
)
from config import _STAGING_TTL_S, _USAGE_KINDS_ALL, _now_iso, _LAST_SEEN_FLUSH_INTERVAL_SECONDS


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect STAGING_DIR
    via `monkeypatch.setattr(main, "STAGING_DIR", ...)` and stub `main._geo_lookup`,
    so these mutable runtime values are read through `main` (the patch target).
    main re-exports them from config, so production reads the same config values.
    Call-time import: no load-time background<->main cycle (and background must not
    import deps/paths, which this respects — main is only touched at call time)."""
    import main
    return main


def _current_jti(access_token, *, verify_exp: bool = True):
    """Decode `jti` from an access_token cookie. main.py's _record_usage_event
    used the deps-side _current_jti, but per the DAG background must NOT import
    deps, so the trivial decode is inlined here — same behaviour: returns the
    token's jti or None, never raises (a missing/invalid token yields None)."""
    if not access_token:
        return None
    try:
        return jwt.decode(
            access_token, SECRET_KEY, algorithms=[ALGORITHM],
            options={"verify_exp": verify_exp},
        ).get("jti")
    except jwt.PyJWTError:
        return None

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
    # Call through `main` so a test that stubs `main._send_legal_blast_emails`
    # (and/or `main.threading.Thread`) intercepts the blast — preserves the
    # pre-refactor behaviour where both lived in main's namespace.
    threading.Thread(target=_m()._send_legal_blast_emails, daemon=True).start()

def _persist_session(db: Session, jti: str, sess: dict) -> None:
    """Write-through one session into `auth_sessions` so it survives a restart.
    Best-effort: a failure here must never break login — the session still works
    for this process lifetime via the in-memory map, it just won't be rehydrated
    after the next restart. Datetimes are stored as ISO-8601 UTC strings."""
    try:
        # add(), not merge(): jti is a fresh UUID per mint so the row can't
        # already exist — merge()'s SELECT-before-INSERT is wasted, and an
        # accidental duplicate jti should surface as the caught IntegrityError
        # rather than silently overwriting created_at/ip/ua.
        db.add(models.AuthSession(
            jti=jti,
            user_id=sess["user_id"],
            email=sess["email"],
            ip_address=sess.get("ip_address"),
            user_agent=sess.get("user_agent"),
            created_at=sess["created_at"].isoformat(),
            last_seen_at=sess["last_seen_at"].isoformat(),
            expires_at=sess["expires_at"].isoformat(),
        ))
        db.commit()
    except Exception:
        logging.exception("auth session persist failed for jti=%s", jti)
        db.rollback()

def _delete_session(db: Session, jti: str) -> None:
    """Delete a persisted session row and commit, raising on DB failure (after a
    rollback). Revocation must be durable: a session evicted from
    `_active_sessions` while its row survives on disk would be resurrected by
    `_load_active_sessions()` on the next restart, so callers evict from memory
    only *after* this returns successfully. The commit also flushes any rows the
    caller staged in the same transaction (e.g. the audit row in admin
    termination), so the audit and the delete land — or roll back — together."""
    try:
        db.query(models.AuthSession).filter(models.AuthSession.jti == jti).delete(
            synchronize_session=False
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

def _delete_user_sessions(db: Session, user_id: int) -> None:
    """Delete every persisted auth_sessions row for a user and commit. Used when
    a user is deactivated so a restart can't rehydrate their sessions. The commit
    also flushes anything else staged on `db` in the same transaction (e.g. the
    is_active change + audit row). Re-raises on DB failure after a rollback, like
    `_delete_session` — revocation must be durable before the caller evicts the
    matching entries from `_active_sessions`."""
    try:
        db.query(models.AuthSession).filter(
            models.AuthSession.user_id == user_id
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise

def _purge_expired_session_rows(db: Session) -> None:
    """Delete `auth_sessions` rows past their expiry. Called once at startup by
    `_load_active_sessions` — the only point expired rows are ever read, so the
    startup sweep is sufficient and the login path needn't purge per-request.
    Best-effort — pure cleanup, never on a request's critical path."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        db.query(models.AuthSession).filter(
            models.AuthSession.expires_at < now_iso
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        logging.exception("auth session expiry purge failed")
        db.rollback()

def _load_active_sessions() -> None:
    """Rehydrate `_active_sessions` from the `auth_sessions` table on startup so
    a backend restart no longer bounces every logged-in user to /login. Expired
    rows are swept first (they're also reaped on every login, so they can't grow
    unbounded between restarts). Best-effort: on any failure we leave the map
    as-is, degrading to the old behaviour (everyone re-logs-in) rather than
    blocking boot."""
    db = SessionLocal()
    try:
        _purge_expired_session_rows(db)
        rows = db.query(models.AuthSession).all()
        loaded = 0
        with _active_sessions_lock:
            for r in rows:
                try:
                    _active_sessions[r.jti] = {
                        "user_id": r.user_id,
                        "email": r.email,
                        "ip_address": r.ip_address,
                        "user_agent": r.user_agent,
                        "created_at": datetime.fromisoformat(r.created_at),
                        "last_seen_at": datetime.fromisoformat(r.last_seen_at),
                        "expires_at": datetime.fromisoformat(r.expires_at),
                    }
                    loaded += 1
                except Exception:
                    logging.exception("skipping unparseable auth session jti=%s", r.jti)
        logging.info("rehydrated %d active session(s) from auth_sessions", loaded)
    except Exception:
        logging.exception("startup: failed to rehydrate auth sessions; sessions will re-login")
        db.rollback()
    finally:
        db.close()

def _flush_last_seen(db: Session) -> None:
    """Mirror the in-memory _last_seen presence map into users.last_seen_at.

    The hot auth deps (get_current_user / get_optional_user) deliberately do no
    per-request DB writes — they only stamp the in-memory _last_seen map. This
    flush persists that map so Admin → Users can show a last-seen time for
    *every* user, not just those with a live session. Run periodically by the
    _lifespan flush loop and once more on clean shutdown.

    Forward-only: the per-user UPDATE never moves a timestamp backwards, so it
    can't undo the 0015 backfill (or a newer flush) and racing flushes are
    idempotent. We snapshot the map under the lock, then do all DB I/O outside
    it (same discipline as _persist_session — never hold the threading lock
    across SQLite I/O). Best-effort: a failure is logged and retried next tick."""
    with _last_seen_lock:
        snapshot = [(uid, ts.isoformat()) for uid, ts in _last_seen.items()]
    if not snapshot:
        return
    try:
        for uid, ts_iso in snapshot:
            db.execute(
                sa_text(
                    "UPDATE users SET last_seen_at = :ts "
                    "WHERE id = :id AND (last_seen_at IS NULL OR last_seen_at < :ts)"
                ),
                {"ts": ts_iso, "id": uid},
            )
        db.commit()
    except Exception:
        logging.exception("last_seen flush failed")
        db.rollback()

def _flush_last_seen_once() -> None:
    """Open a short-lived session, flush, close. Wrapper so the flush loop can
    offload to a worker thread (`asyncio.to_thread`) and the shutdown path can
    call it directly."""
    db = SessionLocal()
    try:
        _flush_last_seen(db)
    finally:
        db.close()

async def _last_seen_flush_loop() -> None:
    """Periodically persist the presence map (see _flush_last_seen). The DB work
    runs in a worker thread so the blocking SQLite I/O never stalls the event
    loop. Cancelled on shutdown by _lifespan, which then does one final flush."""
    while True:
        await asyncio.sleep(_LAST_SEEN_FLUSH_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(_flush_last_seen_once)
        except Exception:
            logging.exception("last_seen flush loop tick failed")

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

def _audit(
    db: Session,
    actor: models.User | None,
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

    `actor` is None for system/automated events with no authenticated user —
    e.g. the registration lifecycle (self-service register, plus approve/reject
    triggered via the tokenized email links, which carry no session). Such rows
    store actor_user_id NULL; the feed renders them as a "System" actor and the
    leaderboard omits them (they aren't attributable to any admin).
    """
    db.add(models.AdminAuditLog(
        created_at=_now_iso(),
        actor_user_id=actor.id if actor is not None else None,
        action=action,
        target_kind=target_kind,
        target_id=str(target_id) if target_id is not None else None,
        summary=summary,
        # `is not None` rather than truthiness — an intentional `details={}`
        # should still serialise (as the JSON "{}") instead of silently becoming NULL.
        details_json=json.dumps(details, separators=(",", ":")) if details is not None else None,
    ))

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
    if kind not in state._enabled_kinds:
        return
    try:
        ip = request.client.host if request.client else "unknown"
        ua = (request.headers.get("user-agent") or "")[:500] or None
        jti = _current_jti(request.cookies.get("access_token"))
        country, city = _m()._geo_lookup(ip)
        raw_path = path if path is not None else request.url.path
        # Collapse repeated slashes so the log reads cleanly regardless of
        # uvicorn root-path quirks ("/" prefix → "//api/..." in request.url).
        norm_path = re.sub(r"/{2,}", "/", raw_path) if raw_path else raw_path
        db = SessionLocal()
        try:
            # usage_events.hash_id is a FK to books.id. A foreign file that still
            # resolves to a vault hash (an orphan vault symlink with no books row)
            # would otherwise insert a dangling hash → IntegrityError → caught by
            # the outer swallow → the whole event silently dropped. Null it so the
            # open is still logged, identified by `path` like any other foreign
            # file. Also covers the race where a book is deleted between the
            # caller resolving the hash and this insert.
            safe_hash = hash_id
            if safe_hash is not None and db.query(models.Book.id).filter(
                models.Book.id == safe_hash
            ).first() is None:
                safe_hash = None
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
                hash_id=safe_hash,
                extra_json=json.dumps(extra, separators=(",", ":")) if extra is not None else None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        # Telemetry must never break a real request.
        pass

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
        names = os.listdir(_m().STAGING_DIR)
    except FileNotFoundError:
        return
    for name in names:
        path = os.path.join(_m().STAGING_DIR, name)
        if path in live_dirs or not os.path.isdir(path):
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age > _STAGING_TTL_S:
            shutil.rmtree(path, ignore_errors=True)

def _load_enabled_kinds() -> None:
    """Read the persisted enabled-kinds set from app_meta into the module
    cache. Called once at startup and after each admin update. Falls back to
    'all enabled' if the row is missing or malformed (the safer default —
    matches the Privacy Policy's published list)."""
    db = SessionLocal()
    try:
        row = db.query(models.AppMeta).filter(
            models.AppMeta.key == "usage_events_enabled_kinds"
        ).first()
        if row and row.value:
            try:
                vals = json.loads(row.value)
                if isinstance(vals, list):
                    state._enabled_kinds = frozenset(v for v in vals if v in _USAGE_KINDS_ALL)
                    return
            except json.JSONDecodeError:
                pass
        state._enabled_kinds = frozenset(_USAGE_KINDS_ALL)
    finally:
        db.close()
