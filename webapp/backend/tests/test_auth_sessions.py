"""Sessions survive a backend restart.

The in-memory `_active_sessions` allowlist is now write-through mirrored to the
`auth_sessions` table and rehydrated from it on startup, so a uvicorn restart no
longer bounces every logged-in user to /login. These tests pin:
  - login writes a row, logout / admin-terminate delete it,
  - `_load_active_sessions()` repopulates the in-memory map (and a request with a
    matching JWT cookie then succeeds), and
  - expired rows are swept on rehydration rather than resurrected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest


def _mock_geo(monkeypatch, main):
    # Keep the login usage-event path off the network.
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"))


def _jti_for(main, email: str) -> str:
    with main._active_sessions_lock:
        for jti, s in main._active_sessions.items():
            if s["email"] == email:
                return jti
    raise AssertionError(f"no in-memory session for {email}")


def test_login_persists_auth_session_row(app_ctx, monkeypatch):
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _mock_geo(monkeypatch, main)
    helpers["make_user"]("u@x.com")  # password is "x"

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.post("/api/login", json={"email": "u@x.com", "password": "x"})
    assert r.status_code == 200, r.text

    db = TestSession()
    try:
        rows = db.query(models.AuthSession).all()
        assert len(rows) == 1
        assert rows[0].email == "u@x.com"
        # The persisted jti must match the in-memory registration.
        assert rows[0].jti in main._active_sessions
    finally:
        db.close()


def test_logout_deletes_auth_session_row(app_ctx, monkeypatch):
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _mock_geo(monkeypatch, main)
    helpers["make_user"]("u@x.com")

    c = helpers["client_for"]("u@x.com")   # client_for now persists the row too
    jti = _jti_for(main, "u@x.com")

    r = c.post("/api/logout")
    assert r.status_code == 200, r.text

    assert jti not in main._active_sessions
    db = TestSession()
    try:
        assert db.query(models.AuthSession).count() == 0
    finally:
        db.close()


def test_admin_terminate_deletes_auth_session_row(app_ctx, monkeypatch):
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _mock_geo(monkeypatch, main)
    helpers["make_user"]("admin@x.com", admin=True)
    helpers["make_user"]("victim@x.com")

    victim_c = helpers["client_for"]("victim@x.com")  # registers + persists session
    assert victim_c is not None
    victim_jti = _jti_for(main, "victim@x.com")

    admin_c = helpers["client_for"]("admin@x.com")
    r = admin_c.delete(f"/api/admin/sessions/{victim_jti}")
    assert r.status_code == 200, r.text

    assert victim_jti not in main._active_sessions
    db = TestSession()
    try:
        assert db.query(models.AuthSession).filter_by(jti=victim_jti).count() == 0
        # The termination audit row was committed alongside the row delete.
        assert db.query(models.AdminAuditLog).filter_by(
            action="user.session_terminate"
        ).count() == 1
    finally:
        db.close()


def test_rehydrate_restores_session_and_request_succeeds(app_ctx):
    """A valid auth_sessions row + matching JWT cookie should authenticate after
    `_load_active_sessions()`, even though the in-memory map was wiped (the
    restart scenario)."""
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("survivor@x.com")

    from security import create_access_token
    token, jti, expires_at = create_access_token({"sub": "survivor@x.com"})
    now = datetime.now(timezone.utc)

    db = TestSession()
    try:
        u = db.query(models.User).filter_by(email="survivor@x.com").first()
        db.add(models.AuthSession(
            jti=jti, user_id=u.id, email="survivor@x.com",
            ip_address="1.2.3.4", user_agent="pytest",
            created_at=now.isoformat(), last_seen_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        ))
        db.commit()
    finally:
        db.close()

    # Simulate the restart: in-memory map is empty before rehydration.
    with main._active_sessions_lock:
        main._active_sessions.clear()

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    c.cookies.set("access_token", token)
    # Without the in-memory entry the gate rejects the (otherwise valid) token.
    assert c.get("/api/me").status_code == 401

    main._load_active_sessions()

    assert jti in main._active_sessions
    r = c.get("/api/me")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "survivor@x.com"


def test_rehydrate_sweeps_expired_rows(app_ctx):
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("u@x.com")

    now = datetime.now(timezone.utc)
    db = TestSession()
    try:
        u = db.query(models.User).filter_by(email="u@x.com").first()
        # One live row, one already expired.
        db.add(models.AuthSession(
            jti="live", user_id=u.id, email="u@x.com",
            created_at=now.isoformat(), last_seen_at=now.isoformat(),
            expires_at=(now + timedelta(days=7)).isoformat(),
        ))
        db.add(models.AuthSession(
            jti="stale", user_id=u.id, email="u@x.com",
            created_at=(now - timedelta(days=8)).isoformat(),
            last_seen_at=(now - timedelta(days=8)).isoformat(),
            expires_at=(now - timedelta(hours=1)).isoformat(),
        ))
        db.commit()
    finally:
        db.close()

    with main._active_sessions_lock:
        main._active_sessions.clear()
    main._load_active_sessions()

    assert "live" in main._active_sessions
    assert "stale" not in main._active_sessions
    db = TestSession()
    try:
        remaining = {r.jti for r in db.query(models.AuthSession).all()}
        assert remaining == {"live"}  # expired row pruned from disk too
    finally:
        db.close()


def test_logout_deletes_expired_session_row(app_ctx):
    """An expired cookie must still let logout clean up its own row — jwt.decode
    is called with verify_exp=False so the jti is extractable past expiry."""
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("u@x.com")

    from security import SECRET_KEY, ALGORITHM
    exp = datetime.now(timezone.utc) - timedelta(hours=1)
    token = jwt.encode({"sub": "u@x.com", "exp": exp, "jti": "expired-jti"},
                       SECRET_KEY, algorithm=ALGORITHM)
    db = TestSession()
    try:
        u = db.query(models.User).filter_by(email="u@x.com").first()
        db.add(models.AuthSession(
            jti="expired-jti", user_id=u.id, email="u@x.com",
            created_at=exp.isoformat(), last_seen_at=exp.isoformat(),
            expires_at=exp.isoformat(),
        ))
        db.commit()
    finally:
        db.close()

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    c.cookies.set("access_token", token)
    assert c.post("/api/logout").status_code == 200

    db = TestSession()
    try:
        assert db.query(models.AuthSession).filter_by(jti="expired-jti").count() == 0
    finally:
        db.close()


def test_logout_keeps_session_when_row_delete_fails(app_ctx, monkeypatch):
    """If the durable delete fails, logout must NOT evict from memory — a session
    gone from memory but still on disk would be resurrected on the next restart.
    Leaving both in place keeps the state consistent."""
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("u@x.com")

    c = helpers["client_for"]("u@x.com")   # client_for persists the row
    jti = _jti_for(main, "u@x.com")

    def _boom(_db, _jti):
        raise RuntimeError("simulated DB failure")
    monkeypatch.setattr(main, "_delete_session", _boom)

    assert c.post("/api/logout").status_code == 200   # logout still clears the cookie
    assert jti in main._active_sessions               # but memory is NOT evicted
    db = TestSession()
    try:
        # ...and the persisted row is left in place, so memory and disk agree.
        assert db.query(models.AuthSession).filter_by(jti=jti).count() == 1
    finally:
        db.close()


def test_admin_terminate_does_not_evict_when_delete_fails(app_ctx, monkeypatch):
    """A failed row delete must roll back the whole transaction (audit included)
    and leave the target's session fully live, so a restart can't rehydrate a
    session the admin believes was terminated."""
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    helpers["make_user"]("victim@x.com")

    helpers["client_for"]("victim@x.com")             # registers + persists session
    victim_jti = _jti_for(main, "victim@x.com")
    admin_c = helpers["client_for"]("admin@x.com")

    def _boom(_db, _jti):
        raise RuntimeError("simulated DB failure")
    monkeypatch.setattr(main, "_delete_session", _boom)

    with pytest.raises(Exception):                    # surfaces as a 5xx to the admin
        admin_c.delete(f"/api/admin/sessions/{victim_jti}")

    assert victim_jti in main._active_sessions        # session left fully live in memory
    db = TestSession()
    try:
        # The persisted row survives (disk wasn't torn) and no audit row was
        # committed — the audit was staged on the same transaction the failed
        # delete would have committed, so it never lands.
        assert db.query(models.AuthSession).filter_by(jti=victim_jti).count() == 1
        assert db.query(models.AdminAuditLog).filter_by(
            action="user.session_terminate"
        ).count() == 0
    finally:
        db.close()


def test_login_then_rehydrate_round_trips(app_ctx, monkeypatch):
    """End-to-end: a row written by the real /api/login -> _persist_session path
    must parse back through _load_active_sessions, so a format drift between the
    write and read sides can't slip past (the hand-built test_rehydrate_* rows
    don't exercise _persist_session's serialization)."""
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _mock_geo(monkeypatch, main)
    # Non-secure cookie so the http TestClient keeps the Set-Cookie and the full
    # browser round-trip works (matches the dev/LAN plain-http config).
    monkeypatch.setenv("COOKIE_SECURE", "false")
    helpers["make_user"]("u@x.com")

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    assert c.post("/api/login", json={"email": "u@x.com", "password": "x"}).status_code == 200

    # Simulate a restart: wipe the in-memory map, leaving only the persisted row.
    with main._active_sessions_lock:
        main._active_sessions.clear()
    assert c.get("/api/me").status_code == 401   # gate rejects with an empty map

    main._load_active_sessions()                 # parses the row login just wrote

    r = c.get("/api/me")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "u@x.com"


def test_deactivate_user_terminates_sessions(app_ctx):
    """Deactivating a user deletes their auth_sessions rows AND evicts their
    in-memory sessions, so a restart can't rehydrate them and the old cookie
    stops authenticating immediately."""
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    victim_id = helpers["make_user"]("victim@x.com")

    victim_c = helpers["client_for"]("victim@x.com")   # registers + persists session
    victim_jti = _jti_for(main, "victim@x.com")
    assert victim_c.get("/api/me").status_code == 200  # live before deactivation

    admin_c = helpers["client_for"]("admin@x.com")
    r = admin_c.put(f"/api/admin/users/{victim_id}/clearance", json={"is_active": False})
    assert r.status_code == 200, r.text

    assert victim_jti not in main._active_sessions     # evicted from memory
    assert victim_c.get("/api/me").status_code == 401  # old cookie no longer authenticates
    db = TestSession()
    try:
        assert db.query(models.AuthSession).filter_by(user_id=victim_id).count() == 0
    finally:
        db.close()
