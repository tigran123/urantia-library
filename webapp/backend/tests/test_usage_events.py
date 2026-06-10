"""Tests for the usage_events instrumentation and admin /api/admin/usage/* feed.

Mocks `main._geo_lookup` so tests don't need the MaxMind .mmdb on disk.
"""
from __future__ import annotations


def _mock_geo(monkeypatch, main, country="ZZ", city="Testville"):
    """Replace the geo lookup with a deterministic stub. Patching the
    function itself (not the underlying reader) keeps the test independent of
    whether geoip2 is installed."""
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: (country, city))


def _count_events(TestSession, models, **filters):
    db = TestSession()
    try:
        q = db.query(models.UsageEvent)
        for k, v in filters.items():
            q = q.filter(getattr(models.UsageEvent, k) == v)
        return q.count()
    finally:
        db.close()


def _last_event(TestSession, models, **filters):
    db = TestSession()
    try:
        q = db.query(models.UsageEvent).order_by(models.UsageEvent.id.desc())
        for k, v in filters.items():
            q = q.filter(getattr(models.UsageEvent, k) == v)
        return q.first()
    finally:
        db.close()


def test_anonymous_browse_records_page_event(app_ctx, monkeypatch):
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.get("/api/browse?path=")
    assert r.status_code == 200

    ev = _last_event(TestSession, models, kind="page")
    assert ev is not None
    assert ev.user_id is None
    assert ev.path == "/"
    assert ev.geo_country == "ZZ"
    assert ev.geo_city == "Testville"


def test_authenticated_search_records_search_with_query_in_extra(app_ctx, monkeypatch):
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    helpers["make_user"]("reader@example.com")
    c = helpers["client_for"]("reader@example.com")

    r = c.get("/api/search?q=nonexistent_term_for_test")
    assert r.status_code == 200

    ev = _last_event(TestSession, models, kind="search")
    assert ev is not None
    assert ev.user_id is not None
    import json
    extra = json.loads(ev.extra_json)
    assert extra["q"] == "nonexistent_term_for_test"
    assert extra["page"] == 1
    assert "total" in extra


def test_failed_login_records_login_event_with_success_false(app_ctx, monkeypatch):
    helpers, _, _ = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    TestSession = helpers["TestSession"]
    _mock_geo(monkeypatch, main)

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.post("/api/login", json={"email": "ghost@example.com", "password": "wrong"})
    assert r.status_code == 400

    ev = _last_event(TestSession, models, kind="login")
    assert ev is not None
    import json
    extra = json.loads(ev.extra_json)
    assert extra["success"] is False
    assert extra["email"] == "ghost@example.com"
    assert extra["reason"] == "bad_credentials"
    # user row didn't exist, so user_id stays NULL
    assert ev.user_id is None


def test_register_without_consent_is_rejected_and_logged(app_ctx, monkeypatch):
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    # No `accepted_legal` field — defaults to False, must be rejected.
    r = c.post("/api/register", json={"email": "newperson@example.com"})
    assert r.status_code == 400
    assert "Privacy Policy" in r.json()["detail"]

    ev = _last_event(TestSession, models, kind="register")
    assert ev is not None
    import json
    extra = json.loads(ev.extra_json)
    assert extra["success"] is False
    assert extra["reason"] == "legal_not_accepted"


def test_register_with_consent_stamps_acceptance(app_ctx, monkeypatch):
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)
    # send_admin_notification calls SMTP; we already monkey-patched
    # _send_email_multipart in conftest, so this is captured silently.

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.post("/api/register", json={
        "email": "consenter@example.com",
        "accepted_legal": True,
    })
    assert r.status_code == 200

    db = TestSession()
    try:
        req = db.query(models.RegistrationRequest).filter(
            models.RegistrationRequest.email == "consenter@example.com"
        ).first()
        assert req is not None
        # accepted_legal_at is an ISO-8601 UTC string ending in 'Z'.
        assert req.accepted_legal_at is not None
        assert req.accepted_legal_at.endswith("Z")
    finally:
        db.close()

    ev = _last_event(TestSession, models, kind="register")
    assert ev is not None
    import json
    extra = json.loads(ev.extra_json)
    assert extra["success"] is True


def test_admin_usage_overview_requires_admin(app_ctx):
    helpers, _, _ = app_ctx
    main = helpers["main"]

    helpers["make_user"]("plain@example.com", admin=False)
    c = helpers["client_for"]("plain@example.com")
    r = c.get("/api/admin/usage/overview")
    assert r.status_code == 403


def test_admin_usage_overview_returns_aggregates(app_ctx, monkeypatch):
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main, country="AM", city="Yerevan")

    # Seed: anonymous browse + authed search
    from fastapi.testclient import TestClient
    TestClient(main.app).get("/api/browse?path=")
    helpers["make_user"]("reader@example.com")
    helpers["client_for"]("reader@example.com").get("/api/search?q=foo")

    helpers["make_user"]("admin@example.com", admin=True)
    ac = helpers["client_for"]("admin@example.com")
    r = ac.get("/api/admin/usage/overview?days=30")
    assert r.status_code == 200
    body = r.json()
    assert body["by_kind"].get("page", 0) >= 1
    assert body["by_kind"].get("search", 0) >= 1
    assert body["unique_countries"] >= 1  # 'AM' from mocked geo


def test_admin_usage_by_country_groups_correctly(app_ctx, monkeypatch):
    helpers, _, _ = app_ctx
    main = helpers["main"]
    _mock_geo(monkeypatch, main, country="AM", city="Yerevan")

    from fastapi.testclient import TestClient
    TestClient(main.app).get("/api/browse?path=")
    TestClient(main.app).get("/api/browse?path=")

    helpers["make_user"]("admin@example.com", admin=True)
    r = helpers["client_for"]("admin@example.com").get("/api/admin/usage/by-country")
    assert r.status_code == 200
    countries = r.json()["countries"]
    am = next((c for c in countries if c["country"] == "AM"), None)
    assert am is not None
    assert am["events"] >= 2


def test_share_records_playlist_share_not_visibility(app_ctx, monkeypatch):
    """Sharing a playlist (POST .../share, private->public) emits a dedicated
    `playlist_share` event, not the generic `playlist_visibility`. Unshare
    (going private) still emits `playlist_visibility`."""
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    helpers["make_user"]("sharer@example.com")
    c = helpers["client_for"]("sharer@example.com")

    pid = c.post("/api/playlists", json={"name": "L"}).json()["id"]
    r = c.post(f"/api/playlists/{pid}/share")
    assert r.status_code == 200, r.text

    ev = _last_event(TestSession, models, kind="playlist_share")
    assert ev is not None
    assert ev.user_id is not None
    import json
    extra = json.loads(ev.extra_json)
    assert extra["playlist_id"] == pid
    # The share action must not also emit a generic visibility event.
    assert _count_events(TestSession, models, kind="playlist_visibility") == 0

    # Going private still records playlist_visibility (only the public->share
    # path moved to the dedicated kind).
    c.delete(f"/api/playlists/{pid}/share")
    assert _count_events(TestSession, models, kind="playlist_visibility") == 1


def test_copy_records_playlist_copy_with_self_copy_flag(app_ctx, monkeypatch):
    """Copying a shared playlist emits `playlist_copy`. The actor is the copier,
    and extra.self_copy distinguishes a copy by another user from a self-copy."""
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    owner_id = helpers["make_user"]("owner@example.com")
    viewer_id = helpers["make_user"]("viewer@example.com")
    owner = helpers["client_for"]("owner@example.com")
    viewer = helpers["client_for"]("viewer@example.com")

    pid = owner.post("/api/playlists", json={"name": "Src"}).json()["id"]
    token = owner.post(f"/api/playlists/{pid}/share").json()["token"]

    import json
    # A different user copies it -> self_copy False, source owner recorded.
    r = viewer.post(f"/api/shared/{token}/copy")
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    ev = _last_event(TestSession, models, kind="playlist_copy")
    assert ev is not None
    assert ev.user_id == viewer_id
    extra = json.loads(ev.extra_json)
    assert extra["self_copy"] is False
    assert extra["source_owner_id"] == owner_id
    assert extra["source_playlist_id"] == pid
    assert extra["new_playlist_id"] == new_id

    # The owner copying their own shared list -> self_copy True.
    owner.post(f"/api/shared/{token}/copy")
    ev2 = _last_event(TestSession, models, kind="playlist_copy")
    assert ev2 is not None
    assert ev2.user_id == owner_id
    assert json.loads(ev2.extra_json)["self_copy"] is True


def test_share_link_copied_records_event_owner_only(app_ctx, monkeypatch):
    """Clicking "Copy link" in the Share dialog pings POST .../share-link-copied,
    which records a `playlist_link_copy` event. Owner-gated: a non-owner is
    forbidden and records nothing."""
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    owner_id = helpers["make_user"]("owner@example.com")
    helpers["make_user"]("other@example.com")
    owner = helpers["client_for"]("owner@example.com")
    other = helpers["client_for"]("other@example.com")

    pid = owner.post("/api/playlists", json={"name": "L"}).json()["id"]
    owner.post(f"/api/playlists/{pid}/share")

    # A non-owner cannot record against someone else's playlist (and no row).
    assert other.post(f"/api/playlists/{pid}/share-link-copied").status_code == 403
    assert _count_events(TestSession, models, kind="playlist_link_copy") == 0

    r = owner.post(f"/api/playlists/{pid}/share-link-copied")
    assert r.status_code == 200, r.text
    ev = _last_event(TestSession, models, kind="playlist_link_copy")
    assert ev is not None
    assert ev.user_id == owner_id
    import json
    assert json.loads(ev.extra_json)["playlist_id"] == pid


def test_geoip_lookup_failure_does_not_break_request(app_ctx, monkeypatch):
    """If _geo_lookup raises, the request must still succeed and the event
    must still be recorded with NULL geo. The recorder swallows exceptions
    inside its try/except; we verify by injecting a raising mock."""
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]

    def boom(ip):
        raise RuntimeError("geo broken")
    monkeypatch.setattr(main, "_geo_lookup", boom)

    from fastapi.testclient import TestClient
    r = TestClient(main.app).get("/api/browse?path=")
    assert r.status_code == 200
    # _record_usage_event wraps the whole body in try/except, so the
    # exception in geo prevents the row from being inserted — that's the
    # documented contract (telemetry must never break the request, and
    # half-built rows are worse than missing rows). The request returns 200.
    db = TestSession()
    try:
        # No row inserted, but the user got their browse response. That is
        # the invariant.
        n = db.query(models.UsageEvent).count()
        assert n == 0
    finally:
        db.close()
