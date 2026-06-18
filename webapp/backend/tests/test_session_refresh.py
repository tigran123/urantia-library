"""Sliding sessions — an actively-used login never hits the absolute expiry.

The /api/me heartbeat re-issues the token (same jti, new exp) once it's more
than SESSION_REFRESH_INTERVAL into its lifetime, sets a fresh cookie, and
persists the extended expiry to auth_sessions. A still-fresh token is left
alone, and an idle session (no heartbeat for the full lifetime) still lapses.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _jti_for(main, email: str) -> str:
    with main._active_sessions_lock:
        for jti, s in main._active_sessions.items():
            if s["email"] == email:
                return jti
    raise AssertionError(f"no in-memory session for {email}")


def test_active_session_slides_forward(app_ctx):
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")
    jti = _jti_for(main, "u@x.com")

    # Simulate a token that's well past the refresh interval (only 12 of the
    # 14-day lifetime left, i.e. issued ~2 days ago).
    with main._active_sessions_lock:
        main._active_sessions[jti]["expires_at"] = datetime.now(timezone.utc) + timedelta(days=12)

    r = c.get("/api/me")
    assert r.status_code == 200, r.text
    # A fresh cookie was issued.
    assert "access_token=" in r.headers.get("set-cookie", "")

    now = datetime.now(timezone.utc)
    with main._active_sessions_lock:
        new_exp = main._active_sessions[jti]["expires_at"]
    # Slid back out to ~14 days, and the jti (session identity) is unchanged.
    assert new_exp - now > timedelta(days=13)
    assert jti in main._active_sessions

    # The extended expiry is persisted so a restart keeps the slid window.
    db = TestSession()
    try:
        row = db.query(models.AuthSession).filter(models.AuthSession.jti == jti).first()
        assert row is not None
        assert datetime.fromisoformat(row.expires_at) == new_exp
    finally:
        db.close()


def test_fresh_session_not_refreshed(app_ctx):
    helpers, _emails, _TS = app_ctx
    main = helpers["main"]
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")
    jti = _jti_for(main, "u@x.com")

    with main._active_sessions_lock:
        old_exp = main._active_sessions[jti]["expires_at"]

    r = c.get("/api/me")
    assert r.status_code == 200, r.text
    # A token barely a moment old is left alone — no re-issue, no new cookie.
    assert "access_token=" not in r.headers.get("set-cookie", "")
    with main._active_sessions_lock:
        assert main._active_sessions[jti]["expires_at"] == old_exp
