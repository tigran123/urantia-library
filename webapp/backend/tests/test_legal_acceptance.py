"""Tests for the LEGAL_VERSION re-acceptance feature.

When the operator materially edits one of the Privacy / Terms .md files, they
bump `LEGAL_VERSION` in database.py. Authenticated users whose
`users.legal_version_accepted` no longer matches see a re-acceptance modal,
backed by `/api/me`'s derived `legal_acceptance_current` flag. The user clears
the modal with `POST /api/legal/accept`, which restamps both
`accepted_legal_at` and `legal_version_accepted`. At startup the backend
mails every active user a courtesy notice (throttled via
`app_meta.legal_blast_version`).

These tests pin:
  1-4. /api/me + /api/legal/accept happy path + idempotency.
  5.   Anonymous /api/legal/accept rejected.
  6.   Registration submit stamps legal_version_accepted on the request row.
  7.   Email blast helper sends one mail per active user (skips inactive).
  8.   Throttle claim is a no-op when legal_blast_version matches LEGAL_VERSION.
  9.   Throttle claim fires the blast thread when LEGAL_VERSION advances.
"""
from __future__ import annotations

from sqlalchemy import text as sa_text


def test_me_reports_stale_acceptance(app_ctx):
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("u@x.com")

    db = TestSession()
    try:
        u = db.query(models.User).filter_by(email="u@x.com").first()
        u.legal_version_accepted = "1999-01-01"
        db.commit()
    finally:
        db.close()

    c = helpers["client_for"]("u@x.com")
    r = c.get("/api/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["legal_version_accepted"] == "1999-01-01"
    assert body["legal_acceptance_current"] is False


def test_post_legal_accept_updates_both_columns(app_ctx):
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")

    # Seed the row with stale acceptance.
    db = TestSession()
    try:
        u = db.query(models.User).filter_by(email="u@x.com").first()
        u.legal_version_accepted = "1999-01-01"
        u.accepted_legal_at = "2020-01-01T00:00:00+00:00"
        db.commit()
        original_ts = u.accepted_legal_at
    finally:
        db.close()

    r = c.post("/api/legal/accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["legal_acceptance_current"] is True
    from database import LEGAL_VERSION
    assert body["legal_version_accepted"] == LEGAL_VERSION

    db = TestSession()
    try:
        u = db.query(models.User).filter_by(email="u@x.com").first()
        assert u.legal_version_accepted == LEGAL_VERSION
        assert u.accepted_legal_at != original_ts          # restamped
    finally:
        db.close()


def test_legal_accept_writes_audit_row(app_ctx):
    """POST /api/legal/accept must leave one legal.accept row in admin_audit_log
    with the new version in details, so the operator can later answer
    "did user X accept version V?" without trusting the user row alone."""
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    user_id = helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")

    # Stale the row so the accept actually transitions a version.
    db = TestSession()
    try:
        u = db.query(models.User).filter_by(email="u@x.com").first()
        u.legal_version_accepted = "1999-01-01"
        db.commit()
    finally:
        db.close()

    r = c.post("/api/legal/accept")
    assert r.status_code == 200, r.text

    db = TestSession()
    try:
        rows = db.query(models.AdminAuditLog).filter_by(action="legal.accept").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_user_id == user_id
        assert row.target_kind == "user" and row.target_id == str(user_id)
        import json
        from database import LEGAL_VERSION
        d = json.loads(row.details_json)
        assert d["version"] == LEGAL_VERSION
        assert d["previous_version"] == "1999-01-01"
    finally:
        db.close()


def test_me_flips_to_current_after_accept(app_ctx):
    helpers, _captured, TestSession = app_ctx
    helpers["make_user"]("u@x.com")
    c = helpers["client_for"]("u@x.com")

    # Make stale, accept, re-check /api/me.
    db = TestSession()
    try:
        u = db.query(helpers["models"].User).filter_by(email="u@x.com").first()
        u.legal_version_accepted = "1999-01-01"
        db.commit()
    finally:
        db.close()

    assert c.get("/api/me").json()["legal_acceptance_current"] is False
    c.post("/api/legal/accept")
    assert c.get("/api/me").json()["legal_acceptance_current"] is True


def test_anonymous_legal_accept_is_401(app_ctx):
    helpers, _captured, _TestSession = app_ctx
    from fastapi.testclient import TestClient
    main = helpers["main"]
    c = TestClient(main.app)            # no cookie ⇒ anonymous
    r = c.post("/api/legal/accept")
    assert r.status_code == 401, r.text


def test_register_stamps_legal_version(app_ctx):
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.post("/api/register", json={
        "email": "newcomer@x.com",
        "accepted_legal": True,
        "language": "en",
    })
    assert r.status_code == 200, r.text
    db = TestSession()
    try:
        req = db.query(models.RegistrationRequest).filter_by(email="newcomer@x.com").first()
        assert req is not None
        from database import LEGAL_VERSION
        assert req.legal_version_accepted == LEGAL_VERSION
        assert req.accepted_legal_at is not None
    finally:
        db.close()


def test_blast_emails_all_active_users_only(app_ctx):
    helpers, captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    # Two active + one inactive.
    helpers["make_user"]("a@x.com")
    helpers["make_user"]("b@x.com")
    db = TestSession()
    try:
        inactive = models.User(
            email="c@x.com",
            hashed_password="x",
            is_active=False,
            is_admin=False,
            clearance=0,
            real_name="C",
        )
        db.add(inactive)
        db.commit()
    finally:
        db.close()

    # Reset any blast accumulated at import time.
    captured.clear()
    main._send_legal_blast_emails()

    sent_to = sorted(e["to"] for e in captured)
    assert sent_to == ["a@x.com", "b@x.com"], sent_to
    # Bilingual body sanity-check: subject mentions both languages.
    assert "Updated" in captured[0]["subj"] and "Обновлены" in captured[0]["subj"]


def test_blast_throttle_noop_when_matching(app_ctx, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    from database import LEGAL_VERSION, engine

    # Seed throttle key at the current version (matches what migrations/0004
    # does at deploy time — see "no spam on rollout" in the design plan).
    with engine.begin() as conn:
        conn.execute(sa_text(
            "INSERT OR REPLACE INTO app_meta(key, value) "
            "VALUES ('legal_blast_version', :v)"
        ), {"v": LEGAL_VERSION})

    fired = []
    monkeypatch.setattr(main, "_send_legal_blast_emails", lambda: fired.append(1))
    # Swallow the thread machinery — for this test we only care whether the
    # claim was made, not whether the worker ran.
    monkeypatch.setattr(main.threading, "Thread",
                        lambda target, daemon: type("FakeT", (), {"start": lambda self: target()})())

    main._maybe_send_legal_blast()
    assert fired == [], "blast must not fire when throttle matches LEGAL_VERSION"


def test_blast_throttle_fires_when_version_advances(app_ctx, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    from database import engine

    # Throttle key behind the current LEGAL_VERSION — simulates the operator
    # having just bumped database.py.
    with engine.begin() as conn:
        conn.execute(sa_text(
            "INSERT OR REPLACE INTO app_meta(key, value) "
            "VALUES ('legal_blast_version', '1999-01-01')"
        ))

    fired = []
    monkeypatch.setattr(main, "_send_legal_blast_emails", lambda: fired.append(1))
    monkeypatch.setattr(main.threading, "Thread",
                        lambda target, daemon: type("FakeT", (), {"start": lambda self: target()})())

    main._maybe_send_legal_blast()
    assert fired == [1], "blast should fire exactly once when version advances"

    # Second call is now a no-op (the conditional UPDATE claimed the row).
    main._maybe_send_legal_blast()
    assert fired == [1], "second call must not re-fire — claim is one-shot per version"


def test_blast_seeded_throttle_does_not_fire_on_rollout(app_ctx, monkeypatch):
    """At rollout time, migrations/0004 seeds legal_blast_version equal to the
    new LEGAL_VERSION, so the very first backend restart doesn't spam every
    user. Mirrors test_blast_throttle_noop_when_matching but explicit about
    the deploy-time scenario."""
    helpers, captured, _TestSession = app_ctx
    main = helpers["main"]
    from database import LEGAL_VERSION, engine

    helpers["make_user"]("a@x.com")     # active user that COULD be mailed
    with engine.begin() as conn:
        conn.execute(sa_text(
            "INSERT OR REPLACE INTO app_meta(key, value) "
            "VALUES ('legal_blast_version', :v)"
        ), {"v": LEGAL_VERSION})

    fired = []
    monkeypatch.setattr(main, "_send_legal_blast_emails", lambda: fired.append(1))
    monkeypatch.setattr(main.threading, "Thread",
                        lambda target, daemon: type("FakeT", (), {"start": lambda self: target()})())

    captured.clear()
    main._maybe_send_legal_blast()
    assert fired == [], "rollout must not mail anyone — throttle matches"
    assert captured == [], "no emails captured — the worker was never called"
