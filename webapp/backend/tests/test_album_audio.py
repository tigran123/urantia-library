"""M2: the ?probe=1 usage-logging bypass is removed, and /api/album-subtree exists.

These browse the real BOOKS_DIR root anonymously (like test_usage_events.py). For a
clearance-gated library the anonymous album walk returns no groups and stays cheap;
the point here is endpoint wiring + that logging can no longer be opted out of.
"""
from __future__ import annotations


def _mock_geo(monkeypatch, main):
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"))


def _page_count(TestSession, models):
    db = TestSession()
    try:
        return db.query(models.UsageEvent).filter(models.UsageEvent.kind == "page").count()
    finally:
        db.close()


def test_browse_probe_param_no_longer_suppresses_page_event(app_ctx, monkeypatch):
    """`?probe=1` used to skip _record_usage_event; it's now just an ignored query
    param, so a browse with it must still record a page event (no audit bypass)."""
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.get("/api/browse?path=&probe=1")
    assert r.status_code == 200
    assert _page_count(TestSession, models) >= 1


def test_files_probe_param_no_longer_accepted_as_a_real_param(app_ctx, monkeypatch):
    """`probe` is gone from /api/files too — a bogus path still 404s (and the
    unknown query param is harmless), proving the param isn't wired up anymore."""
    helpers, _, _ = app_ctx
    main = helpers["main"]
    _mock_geo(monkeypatch, main)

    from fastapi.testclient import TestClient
    r = TestClient(main.app).get("/api/files/__does_not_exist__.mp3?probe=1")
    assert r.status_code == 404


def test_album_subtree_returns_grouped_shape_and_does_not_log_page(app_ctx, monkeypatch):
    """album-subtree is a data fetch for a directory the user already navigated to
    (which /api/browse logged a `page` event for), so it must record no page event
    of its own — otherwise the directory's page views are double-counted."""
    helpers, _, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _mock_geo(monkeypatch, main)

    from fastapi.testclient import TestClient
    before = _page_count(TestSession, models)
    r = TestClient(main.app).get("/api/album-subtree?path=")
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == ""
    assert isinstance(body["groups"], list)
    for g in body["groups"]:
        assert {"path", "name", "tracks"} <= set(g.keys())
        assert isinstance(g["tracks"], list)

    # The navigation is logged by /api/browse; album-subtree adds no page event.
    assert _page_count(TestSession, models) == before
