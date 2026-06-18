"""Router module (extracted from main.py): admin user + session management —
the Users panel (list + presence), the live Sessions panel (list + terminate),
and the clearance/role/active update endpoint (which durably revokes sessions on
deactivation). Moved verbatim from main.py (no logic change)."""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException, Depends, Cookie
from sqlalchemy.orm import Session

import models
import schemas
import state
from database import get_db
from config import ONLINE_WINDOW
from deps import require_admin, _current_jti
# _audit / _delete_session / _delete_user_sessions are reached through `main`
# (call-time import inside the routes below) rather than imported here, because
# tests patch them as `main._delete_session` etc. and expect the patch to take
# effect on this route — see tests/test_auth_sessions.py. main re-exports them
# from background, so production calls the same functions. No load-time cycle.

router = APIRouter()


# ---------------- Admin: clearance management ----------------

def _admin_user_row(u: models.User, live: dict[int, datetime], cutoff: datetime) -> dict:
    """Shape a User row for the Admin → Users panel. The displayed last_seen_at
    is the best of the durable users.last_seen_at column and the live _last_seen
    map (the flush lags by up to _LAST_SEEN_FLUSH_INTERVAL_SECONDS, so an active
    user's most-recent activity may live only in the map). is_online, however, is
    derived from the live map alone — the same notion as the footer online count
    and the Sessions panel; a recent *persisted* value (e.g. right after a
    restart, before the next heartbeat) doesn't by itself count as online."""
    live_ts = live.get(u.id)
    effective = live_ts
    if u.last_seen_at:
        try:
            persisted = datetime.fromisoformat(u.last_seen_at)
            if persisted.tzinfo is None:
                persisted = persisted.replace(tzinfo=timezone.utc)
        except ValueError:
            persisted = None
        if persisted is not None and (effective is None or persisted > effective):
            effective = persisted
    return {
        "id": u.id,
        "email": u.email,
        "is_admin": u.is_admin,
        "clearance": u.clearance,
        "is_active": u.is_active,
        "avatar_url": u.avatar_url,
        "real_name": u.real_name,
        "last_seen_at": effective.isoformat() if effective else None,
        "is_online": live_ts is not None and live_ts >= cutoff,
    }


@router.get("/api/admin/users", response_model=List[schemas.AdminUserSummary])
async def admin_list_users(
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(models.User).order_by(models.User.email).all()
    cutoff = datetime.now(timezone.utc) - ONLINE_WINDOW
    with state._last_seen_lock:
        live = dict(state._last_seen)
    return [_admin_user_row(u, live, cutoff) for u in users]


@router.get("/api/admin/sessions", response_model=List[schemas.AdminSessionSummary])
async def admin_list_sessions(
    access_token: str | None = Cookie(None),
    _admin: models.User = Depends(require_admin),
):
    self_jti = _current_jti(access_token)
    with state._active_sessions_lock:
        state._purge_expired_sessions_locked()
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
            for jti, s in state._active_sessions.items()
        ]
    rows.sort(key=lambda r: r["last_seen_at"], reverse=True)
    return rows


@router.delete("/api/admin/sessions/{jti}")
async def admin_terminate_session(
    jti: str,
    access_token: str | None = Cookie(None),
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if jti == _current_jti(access_token):
        raise HTTPException(status_code=400, detail="Refusing to terminate own session")
    # Peek at the session row first so we have something to audit; only after the
    # audit row AND the persisted-session delete have committed do we evict from
    # memory. _delete_session commits both (the audit row is staged in the same
    # transaction) and re-raises on failure — so if it fails the admin sees a
    # 5xx and the target keeps a fully-live session on both memory and disk: no
    # torn-write window where the user is "terminated" in memory but the row
    # survives on disk to be rehydrated on the next restart (nor one where the
    # log claims a termination that didn't happen).
    import main
    with state._active_sessions_lock:
        target = state._active_sessions.get(jti)
    if target is None:
        raise HTTPException(status_code=404, detail="Session not found")
    main._audit(db, admin, "user.session_terminate",
           target_kind="user", target_id=target["user_id"],
           summary=f'Terminated session of {target["email"]}',
           details={"email": target["email"], "jti": jti, "ip_address": target.get("ip_address")})
    main._delete_session(db, jti)
    with state._active_sessions_lock:
        state._active_sessions.pop(jti, None)   # idempotent — another request might have evicted concurrently
    return {"jti": jti, "terminated": True}


@router.put("/api/admin/users/{user_id}/clearance", response_model=schemas.AdminUserSummary)
async def admin_set_user_clearance(
    user_id: int,
    payload: schemas.UserClearanceUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    import main
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
        main._audit(db, admin, "user.clearance",
               target_kind="user", target_id=user.id,
               summary=f'Updated {user.email}: {", ".join(diff.keys())}',
               details={"email": user.email, "changed": diff})
    if diff.get("is_active") == [True, False]:
        # Deactivation durably revokes the user's sessions: delete their
        # auth_sessions rows (so a restart can't rehydrate them) and evict the
        # in-memory entries (so the next request 401s immediately and they must
        # re-login even if reactivated). Delete-then-evict, same ordering as
        # logout / admin_terminate_session; the commit also flushes the
        # is_active change + audit row staged above in the same transaction.
        main._delete_user_sessions(db, user.id)
        with state._active_sessions_lock:
            for j in [k for k, v in state._active_sessions.items() if v["user_id"] == user.id]:
                state._active_sessions.pop(j, None)
    else:
        db.commit()
    db.refresh(user)
    with state._last_seen_lock:
        live = dict(state._last_seen)
    return _admin_user_row(user, live, datetime.now(timezone.utc) - ONLINE_WINDOW)
