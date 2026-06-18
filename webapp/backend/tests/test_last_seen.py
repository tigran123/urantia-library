"""users.last_seen_at — Admin → Users shows last activity for *every* user.

The hot auth deps stamp only the in-memory _last_seen map; a periodic flush (and
a final flush on clean shutdown) mirrors it into users.last_seen_at so offline
users still carry a last-seen time. /api/admin/users merges the persisted column
with the live map (the live, not-yet-flushed value wins) and flags is_online for
activity within ONLINE_WINDOW.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _row_for(rows, email):
    for r in rows:
        if r["email"] == email:
            return r
    raise AssertionError(f"{email} not in /api/admin/users response")


def _no_geo(monkeypatch, main):
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"))


def test_active_user_is_online_with_timestamp(app_ctx, monkeypatch):
    helpers, _emails, _TS = app_ctx
    main = helpers["main"]
    _no_geo(monkeypatch, main)
    helpers["make_user"]("admin@x.com", admin=True)
    helpers["make_user"]("u@x.com")

    # An authed request stamps the in-memory _last_seen map.
    uc = helpers["client_for"]("u@x.com")
    assert uc.get("/api/me").status_code == 200

    ac = helpers["client_for"]("admin@x.com")
    r = ac.get("/api/admin/users")
    assert r.status_code == 200, r.text
    row = _row_for(r.json(), "u@x.com")
    assert row["is_online"] is True
    assert row["last_seen_at"] is not None


def test_never_seen_user_has_no_timestamp(app_ctx, monkeypatch):
    helpers, _emails, _TS = app_ctx
    main = helpers["main"]
    _no_geo(monkeypatch, main)
    helpers["make_user"]("admin@x.com", admin=True)
    helpers["make_user"]("ghost@x.com")  # never makes a request

    ac = helpers["client_for"]("admin@x.com")
    row = _row_for(ac.get("/api/admin/users").json(), "ghost@x.com")
    assert row["last_seen_at"] is None
    assert row["is_online"] is False


def test_flush_persists_and_survives_going_offline(app_ctx, monkeypatch):
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _no_geo(monkeypatch, main)
    helpers["make_user"]("admin@x.com", admin=True)
    uid = helpers["make_user"]("u@x.com")

    uc = helpers["client_for"]("u@x.com")
    assert uc.get("/api/me").status_code == 200

    # Mirror the in-memory map into users.last_seen_at.
    main._flush_last_seen_once()
    db = TestSession()
    try:
        persisted = db.query(models.User).filter(models.User.id == uid).first().last_seen_at
    finally:
        db.close()
    assert persisted is not None

    # Simulate going offline / a restart pruning the live presence map.
    with main._last_seen_lock:
        main._last_seen.clear()

    ac = helpers["client_for"]("admin@x.com")
    row = _row_for(ac.get("/api/admin/users").json(), "u@x.com")
    assert row["last_seen_at"] is not None   # still shown, from the persisted column
    assert row["is_online"] is False         # but no longer counted as online


def test_flush_is_forward_only(app_ctx, monkeypatch):
    helpers, _emails, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    _no_geo(monkeypatch, main)
    uid = helpers["make_user"]("u@x.com")

    future = datetime(2999, 1, 1, tzinfo=timezone.utc).isoformat()
    db = TestSession()
    try:
        u = db.query(models.User).filter(models.User.id == uid).first()
        u.last_seen_at = future
        db.commit()
    finally:
        db.close()

    # An older in-memory stamp must not overwrite the newer persisted value.
    with main._last_seen_lock:
        main._last_seen[uid] = datetime.now(timezone.utc)
    main._flush_last_seen_once()

    db = TestSession()
    try:
        assert db.query(models.User).filter(models.User.id == uid).first().last_seen_at == future
    finally:
        db.close()
