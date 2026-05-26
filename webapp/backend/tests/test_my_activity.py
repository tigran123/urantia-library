"""Tests for the per-user activity surface (#/account/activity, backed by
GET/DELETE /api/me/activity)."""
from __future__ import annotations


def _mock_geo(monkeypatch, main):
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"))


def test_my_activity_lists_own_events(app_ctx, monkeypatch):
    helpers, _, _ = app_ctx
    main = helpers["main"]
    _mock_geo(monkeypatch, main)

    helpers["make_user"]("alice@example.com")
    c = helpers["client_for"]("alice@example.com")
    # Generate some events. /api/library-stats is deliberately NOT recorded
    # (it's a footer-counter heartbeat, not a page view), so use /api/browse
    # for the page event.
    c.get("/api/browse?path=")
    c.get("/api/search?q=hello")

    r = c.get("/api/me/activity")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    kinds = {ev["kind"] for ev in body["events"]}
    assert "page" in kinds
    assert "search" in kinds


def test_my_activity_does_not_leak_other_users_events(app_ctx, monkeypatch):
    helpers, _, _ = app_ctx
    main = helpers["main"]
    _mock_geo(monkeypatch, main)

    helpers["make_user"]("alice@example.com")
    helpers["make_user"]("bob@example.com")

    alice = helpers["client_for"]("alice@example.com")
    bob = helpers["client_for"]("bob@example.com")

    alice.get("/api/browse?path=")
    bob.get("/api/browse?path=")
    bob.get("/api/search?q=bobs_query_xyz")

    r = alice.get("/api/me/activity")
    rows = r.json()["events"]
    # No bob-flavored events should show up here.
    for ev in rows:
        if ev["extra"] and isinstance(ev["extra"], dict):
            assert ev["extra"].get("q") != "bobs_query_xyz"


def test_delete_my_activity_removes_only_my_rows(app_ctx, monkeypatch):
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    helpers["make_user"]("alice@example.com")
    helpers["make_user"]("bob@example.com")

    alice = helpers["client_for"]("alice@example.com")
    bob = helpers["client_for"]("bob@example.com")

    alice.get("/api/browse?path=")
    alice.get("/api/search?q=foo")
    bob.get("/api/browse?path=")

    r = alice.delete("/api/me/activity")
    assert r.status_code == 200
    assert r.json()["deleted"] >= 2

    # Alice has nothing; Bob still has at least one row.
    db = TestSession()
    try:
        alice_id = db.query(models.User).filter(
            models.User.email == "alice@example.com").first().id
        bob_id = db.query(models.User).filter(
            models.User.email == "bob@example.com").first().id

        n_alice = db.query(models.UsageEvent).filter(
            models.UsageEvent.user_id == alice_id).count()
        n_bob = db.query(models.UsageEvent).filter(
            models.UsageEvent.user_id == bob_id).count()
    finally:
        db.close()

    assert n_alice == 0
    assert n_bob >= 1


def test_my_activity_json_export(app_ctx, monkeypatch):
    helpers, _, _ = app_ctx
    main = helpers["main"]
    _mock_geo(monkeypatch, main)

    helpers["make_user"]("alice@example.com")
    c = helpers["client_for"]("alice@example.com")
    c.get("/api/browse?path=")
    c.get("/api/search?q=anything")

    r = c.get("/api/me/activity?format=json")
    assert r.status_code == 200
    body = r.json()
    assert "exported_at" in body
    assert body["user_email"] == "alice@example.com"
    assert isinstance(body["events"], list)
    assert len(body["events"]) >= 2


def test_my_activity_requires_auth(app_ctx):
    helpers, _, _ = app_ctx
    main = helpers["main"]
    from fastapi.testclient import TestClient
    r = TestClient(main.app).get("/api/me/activity")
    assert r.status_code == 401


def test_legal_meta_is_public(app_ctx):
    helpers, _, _ = app_ctx
    main = helpers["main"]
    from fastapi.testclient import TestClient
    r = TestClient(main.app).get("/api/legal/meta")
    assert r.status_code == 200
    body = r.json()
    assert "contact_email" in body
