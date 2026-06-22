"""The "Forgot your password?" / one-time password-reset flow.

POST /api/forgot-password issues a single-use, 1-hour token and emails a reset
link ONLY for an existing, active account — but always returns the same generic
message (anti-enumeration). POST /api/reset-password consumes the token once,
sets the new password, and terminates every one of the user's sessions.

The request is normally processed on a daemon thread (lookup + token issue +
SMTP, off the request path for anti-enumeration timing); each test that needs the
email monkeypatches main._dispatch_reset_request to run background._process_reset_request
synchronously so the captured-email list / issued token row is deterministic (no
thread-join flakiness). conftest has already patched email_utils._send_email to
append to `captured`.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

GENERIC = "If that email is registered, a password reset link has been sent."


def _mock_geo(monkeypatch, main):
    # Keep the login usage-event path (used in a couple of these tests) offline.
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"))


def _sync_email(monkeypatch, main):
    """Run the reset-request processing synchronously so `captured` (and the
    issued token row) are populated before the request returns. Routes through the
    real background._process_reset_request → send_password_reset_email so the
    user lookup, token issue, and emitted link (and thus the token) are exercised
    end-to-end. The endpoint hands off via main._dispatch_reset_request, so that's
    the patch target."""
    import background
    monkeypatch.setattr(
        main, "_dispatch_reset_request",
        lambda email, lang: background._process_reset_request(email, lang),
    )


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_from_email(email: dict) -> str:
    m = re.search(r"reset-password\?token=([^\"'&\s]+)", email["html"])
    assert m, f"no reset token in email body: {email}"
    return m.group(1)


def _insert_token(TestSession, models, user_id, plaintext, *, created, expires, used=None):
    db = TestSession()
    try:
        db.add(models.PasswordResetToken(
            user_id=user_id,
            token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            created_at=_iso(created),
            expires_at=_iso(expires),
            used_at=_iso(used) if used else None,
        ))
        db.commit()
    finally:
        db.close()


def _make_inactive(TestSession, models, email):
    db = TestSession()
    try:
        u = db.query(models.User).filter_by(email=email).first()
        u.is_active = False
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 1. Happy path: request issues a token + email; reset consumes it.
# --------------------------------------------------------------------------- #
def test_happy_path_request_then_reset(app_ctx, monkeypatch):
    helpers, captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    uid = helpers["make_user"]("u@x.com")

    c = TestClient(main.app)
    r = c.post("/api/forgot-password", json={"email": "u@x.com", "language": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["message"] == GENERIC

    db = TestSession()
    try:
        rows = db.query(models.PasswordResetToken).filter_by(user_id=uid).all()
        assert len(rows) == 1 and rows[0].used_at is None
    finally:
        db.close()
    assert len(captured) == 1 and captured[0]["to"] == "u@x.com"

    token = _token_from_email(captured[0])
    r = c.post("/api/reset-password", json={"token": token, "password": "newpassword123"})
    assert r.status_code == 200, r.text

    db = TestSession()
    try:
        row = db.query(models.PasswordResetToken).filter_by(user_id=uid).first()
        assert row.used_at is not None  # one-time consumption stamped
        assert db.query(models.AdminAuditLog).filter_by(
            action="password.reset_complete"
        ).count() == 1
        assert db.query(models.AdminAuditLog).filter_by(
            action="password.reset_request"
        ).count() == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 2. Nonexistent email: same generic response, no email, no row, no audit.
# --------------------------------------------------------------------------- #
def test_nonexistent_email_is_silent(app_ctx, monkeypatch):
    helpers, captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)

    c = TestClient(main.app)
    r = c.post("/api/forgot-password", json={"email": "nobody@x.com"})
    assert r.status_code == 200
    assert r.json()["message"] == GENERIC
    assert captured == []
    db = TestSession()
    try:
        assert db.query(models.PasswordResetToken).count() == 0
        assert db.query(models.AdminAuditLog).filter_by(
            action="password.reset_request"
        ).count() == 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 3. Inactive user: treated like nonexistent (no email, no token).
# --------------------------------------------------------------------------- #
def test_inactive_user_is_silent(app_ctx, monkeypatch):
    helpers, captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    helpers["make_user"]("ghost@x.com")
    _make_inactive(TestSession, models, "ghost@x.com")

    c = TestClient(main.app)
    r = c.post("/api/forgot-password", json={"email": "ghost@x.com"})
    assert r.status_code == 200 and r.json()["message"] == GENERIC
    assert captured == []
    db = TestSession()
    try:
        assert db.query(models.PasswordResetToken).count() == 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 4. Expired token is rejected; password unchanged.
# --------------------------------------------------------------------------- #
def test_expired_token_rejected(app_ctx):
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    uid = helpers["make_user"]("u@x.com")  # password "x"
    now = _now()
    _insert_token(TestSession, models, uid, "expiredtoken",
                  created=now - timedelta(hours=2), expires=now - timedelta(hours=1))

    c = TestClient(main.app)
    r = c.post("/api/reset-password", json={"token": "expiredtoken", "password": "newpassword123"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid or expired reset link."

    from security import verify_password
    db = TestSession()
    try:
        u = db.query(models.User).filter_by(id=uid).first()
        assert verify_password("x", u.hashed_password)  # original password intact
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 5. A used token can't be reused.
# --------------------------------------------------------------------------- #
def test_used_token_rejected(app_ctx, monkeypatch):
    helpers, captured, TestSession = app_ctx
    main, _models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    helpers["make_user"]("u@x.com")

    c = TestClient(main.app)
    c.post("/api/forgot-password", json={"email": "u@x.com"})
    token = _token_from_email(captured[0])

    assert c.post("/api/reset-password", json={"token": token, "password": "newpassword123"}).status_code == 200
    r = c.post("/api/reset-password", json={"token": token, "password": "anotherpass456"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Invalid or expired reset link."


# --------------------------------------------------------------------------- #
# 6. The password actually changes (new logs in, old fails).
# --------------------------------------------------------------------------- #
def test_password_actually_changes(app_ctx, monkeypatch):
    helpers, captured, TestSession = app_ctx
    main, _models = helpers["main"], helpers["models"]
    _mock_geo(monkeypatch, main)
    _sync_email(monkeypatch, main)
    helpers["make_user"]("u@x.com")  # default password "x"

    c = TestClient(main.app)
    c.post("/api/forgot-password", json={"email": "u@x.com"})
    token = _token_from_email(captured[0])
    assert c.post("/api/reset-password", json={"token": token, "password": "newpassword123"}).status_code == 200

    assert c.post("/api/login", json={"email": "u@x.com", "password": "newpassword123"}).status_code == 200
    assert c.post("/api/login", json={"email": "u@x.com", "password": "x"}).status_code == 400


# --------------------------------------------------------------------------- #
# 7. A successful reset terminates the user's sessions.
# --------------------------------------------------------------------------- #
def test_reset_terminates_sessions(app_ctx, monkeypatch):
    helpers, captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    uid = helpers["make_user"]("victim@x.com")

    victim_c = helpers["client_for"]("victim@x.com")  # registers + persists a session
    assert victim_c.get("/api/me").status_code == 200
    with main._active_sessions_lock:
        victim_jti = next(j for j, s in main._active_sessions.items() if s["email"] == "victim@x.com")

    c = TestClient(main.app)
    c.post("/api/forgot-password", json={"email": "victim@x.com"})
    token = _token_from_email(captured[0])
    assert c.post("/api/reset-password", json={"token": token, "password": "newpassword123"}).status_code == 200

    assert victim_jti not in main._active_sessions       # evicted from memory
    assert victim_c.get("/api/me").status_code == 401     # old cookie no longer authenticates
    db = TestSession()
    try:
        assert db.query(models.AuthSession).filter_by(user_id=uid).count() == 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 8. Anti-enumeration: existing vs nonexistent responses are identical.
# --------------------------------------------------------------------------- #
def test_response_parity_existing_vs_nonexistent(app_ctx, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main, _models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    helpers["make_user"]("real@x.com")

    c = TestClient(main.app)
    r_real = c.post("/api/forgot-password", json={"email": "real@x.com"})
    r_fake = c.post("/api/forgot-password", json={"email": "fake@x.com"})
    assert r_real.status_code == r_fake.status_code == 200
    assert r_real.json() == r_fake.json()


# --------------------------------------------------------------------------- #
# 9. Re-requesting invalidates the prior token (one active token per user).
# --------------------------------------------------------------------------- #
def test_new_request_invalidates_old_token(app_ctx, monkeypatch):
    helpers, captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    uid = helpers["make_user"]("u@x.com")
    now = _now()
    # An older outstanding token (created before the resend window, so a new
    # request will actually issue a fresh one rather than throttle).
    _insert_token(TestSession, models, uid, "oldtoken",
                  created=now - timedelta(minutes=30), expires=now + timedelta(minutes=30))

    c = TestClient(main.app)
    assert c.post("/api/forgot-password", json={"email": "u@x.com"}).status_code == 200
    new_token = _token_from_email(captured[-1])

    db = TestSession()
    try:
        assert db.query(models.PasswordResetToken).filter_by(user_id=uid).count() == 1
    finally:
        db.close()

    # Old link is dead, the freshly issued one works.
    assert c.post("/api/reset-password", json={"token": "oldtoken", "password": "newpassword123"}).status_code == 400
    assert c.post("/api/reset-password", json={"token": new_token, "password": "newpassword123"}).status_code == 200


# --------------------------------------------------------------------------- #
# 9b. Rapid re-requests are throttled: one email, one row.
# --------------------------------------------------------------------------- #
def test_resend_throttle(app_ctx, monkeypatch):
    helpers, captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    uid = helpers["make_user"]("u@x.com")

    c = TestClient(main.app)
    assert c.post("/api/forgot-password", json={"email": "u@x.com"}).status_code == 200
    assert c.post("/api/forgot-password", json={"email": "u@x.com"}).status_code == 200

    assert len(captured) == 1  # second request did not send another email
    db = TestSession()
    try:
        assert db.query(models.PasswordResetToken).filter_by(user_id=uid).count() == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 10. Minimum password length is enforced on reset (and set-password).
# --------------------------------------------------------------------------- #
def test_reset_rejects_short_password(app_ctx, monkeypatch):
    helpers, captured, _TestSession = app_ctx
    main, _models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    helpers["make_user"]("u@x.com")

    c = TestClient(main.app)
    c.post("/api/forgot-password", json={"email": "u@x.com"})
    token = _token_from_email(captured[0])

    r = c.post("/api/reset-password", json={"token": token, "password": "short"})
    assert r.status_code == 400
    assert "at least" in r.json()["detail"]


def test_set_password_rejects_short_password(app_ctx):
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    db = TestSession()
    try:
        db.add(models.RegistrationRequest(email="new@x.com", status="approved", token="rtok"))
        db.commit()
    finally:
        db.close()

    c = TestClient(main.app)
    r = c.post("/api/set-password", json={
        "token": "rtok", "password": "short", "accepted_legal": True,
    })
    assert r.status_code == 400
    assert "at least" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# 11. The unauthenticated endpoint is per-IP rate limited (anti-abuse / DoS).
# --------------------------------------------------------------------------- #
def test_forgot_password_rate_limited(app_ctx, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main, _models = helpers["main"], helpers["models"]
    _sync_email(monkeypatch, main)
    from config import RESET_RL_MAX
    helpers["make_user"]("u@x.com")

    c = TestClient(main.app)
    # The first RESET_RL_MAX requests from one IP are accepted; the next is 429.
    for _ in range(RESET_RL_MAX):
        assert c.post("/api/forgot-password", json={"email": "u@x.com"}).status_code == 200
    assert c.post("/api/forgot-password", json={"email": "u@x.com"}).status_code == 429
    # 429 is IP-based, so it leaks nothing about whether the email exists.
    assert c.post("/api/forgot-password", json={"email": "nobody@x.com"}).status_code == 429


# --------------------------------------------------------------------------- #
# 12. Startup housekeeping sweep deletes expired/used rows, keeps live ones.
# --------------------------------------------------------------------------- #
def test_purge_expired_reset_tokens(app_ctx):
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    uid = helpers["make_user"]("u@x.com")
    now = _now()
    _insert_token(TestSession, models, uid, "livetok",
                  created=now, expires=now + timedelta(hours=1))
    _insert_token(TestSession, models, uid, "expiredtok",
                  created=now - timedelta(hours=2), expires=now - timedelta(hours=1))
    _insert_token(TestSession, models, uid, "usedtok",
                  created=now - timedelta(minutes=5), expires=now + timedelta(hours=1),
                  used=now - timedelta(minutes=1))

    db = TestSession()
    try:
        main._purge_expired_reset_tokens(db)
    finally:
        db.close()

    db = TestSession()
    try:
        remaining = {r.token_hash for r in db.query(models.PasswordResetToken).all()}
    finally:
        db.close()
    assert remaining == {hashlib.sha256(b"livetok").hexdigest()}  # only the live, unused token survives
