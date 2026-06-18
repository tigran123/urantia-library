"""Foundation module (extracted from main.py): FastAPI auth dependencies and
JWT/session helpers — get_current_user / get_optional_user / require_admin,
claim decoding, clearance helpers, and sliding-session refresh. Depends on
state, config, models, database, security. Per the DAG it must NOT import paths;
it may reference background only indirectly (it does not here)."""
import os
import logging
from datetime import datetime, timezone, timedelta
import jwt
from fastapi import Depends, Cookie, HTTPException, status, Response
from sqlalchemy.orm import Session
import models
from database import get_db
from security import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token
from state import _active_sessions, _active_sessions_lock, _last_seen, _last_seen_lock

def _decode_claims(access_token: str | None, *, verify_exp: bool = True) -> dict:
    """Decode an access_token cookie's JWT claims without validating against the
    session map. Returns the claims dict, or `{}` if the cookie is absent or the
    signature/format is invalid. `verify_exp=False` still yields the claims of an
    *expired* token — used by logout, where exp validity is irrelevant when
    tearing a session down (the signature is always verified regardless)."""
    if not access_token:
        return {}
    try:
        return jwt.decode(
            access_token, SECRET_KEY, algorithms=[ALGORITHM],
            options={"verify_exp": verify_exp},
        )
    except jwt.PyJWTError:
        return {}

def _current_jti(access_token: str | None, *, verify_exp: bool = True) -> str | None:
    """Decode `jti` from an access_token cookie without validating against the
    session map. Used by admin handlers that need to know which session is
    the caller's own (to guard against self-termination), and by
    _record_usage_event() to cluster a single login's events."""
    return _decode_claims(access_token, verify_exp=verify_exp).get("jti")

async def get_current_user(access_token: str | None = Cookie(None), db: Session = Depends(get_db)):
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
    access_token: str | None = Cookie(None), db: Session = Depends(get_db)
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

_ACCESS_TOKEN_LIFETIME = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
# Slide an active session forward at most this often. The first /api/me
# heartbeat after the token crosses this age re-issues it for a fresh full
# lifetime, so an actively-used login never reaches the absolute expiry — while
# a session left idle for ACCESS_TOKEN_EXPIRE_MINUTES still lapses and forces a
# re-login. Bounds the re-issue + DB write to ~once/day per active session.
SESSION_REFRESH_INTERVAL = timedelta(days=1)

def _maybe_refresh_session(
    response: Response, access_token: str | None, current_user: models.User, db: Session
) -> None:
    """Sliding-session refresh, called from the /api/me heartbeat. If the
    caller's token is more than SESSION_REFRESH_INTERVAL into its lifetime,
    re-issue it (same jti, new exp) and set a fresh cookie so an actively-used
    login never hits the absolute expiry. Best-effort: any failure leaves the
    current, still-valid cookie in place."""
    jti = _current_jti(access_token)
    if not jti:
        return
    now = datetime.now(timezone.utc)
    with _active_sessions_lock:
        sess = _active_sessions.get(jti)
        if sess is None:
            return
        # Renew only once we're SESSION_REFRESH_INTERVAL past the last issue —
        # derive "time since issue" from the stored expiry and the fixed lifetime.
        if sess["expires_at"] - now > _ACCESS_TOKEN_LIFETIME - SESSION_REFRESH_INTERVAL:
            return
    new_token, _jti, new_expires = create_access_token({"sub": current_user.email}, jti=jti)
    with _active_sessions_lock:
        sess = _active_sessions.get(jti)
        if sess is None:
            return  # terminated between the checks; don't resurrect it via cookie
        sess["expires_at"] = new_expires
    # Persist the slid expiry so a restart rehydrates the extended window and the
    # startup purge doesn't drop a still-active session early. Outside the lock —
    # never hold the threading lock across DB I/O (same discipline as login).
    try:
        db.query(models.AuthSession).filter(models.AuthSession.jti == jti).update(
            {"expires_at": new_expires.isoformat()}, synchronize_session=False
        )
        db.commit()
    except Exception:
        logging.exception("session refresh: failed to persist new expiry jti=%s", jti)
        db.rollback()
    response.set_cookie(
        key="access_token",
        value=new_token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() != "false",
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
