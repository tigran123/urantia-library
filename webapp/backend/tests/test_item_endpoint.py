"""/api/item returns one file's metadata without logging a `page` usage event.

Opening a book used to route through /api/browse on the parent directory just to
fetch the file's metadata, which recorded a phantom `page` event for that
directory on every book open (double-counted alongside the genuine navigation
page view). /api/item is the dedicated lookup that records nothing; /api/browse
still logs genuine navigation (the contrast test pins that).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


def _make_book(tmp_path, monkeypatch, helpers,
               src_rel="Dictionaries/Armenian/slovar.djvu", clearance=0):
    """Point main.{BOOKS_DIR,DATA_DIR} at tmp_path and create a vault file +
    topic symlink + books/book_locations rows. Mirrors test_bulk_dir_actions."""
    main = helpers["main"]
    models = helpers["models"]
    TestSession = helpers["TestSession"]

    books_dir = str(tmp_path)
    data_dir = os.path.join(books_dir, ".data")
    os.makedirs(data_dir, exist_ok=True)
    monkeypatch.setattr(main, "BOOKS_DIR", books_dir, raising=False)
    monkeypatch.setattr(main, "DATA_DIR", data_dir, raising=False)

    hash_id = ("aa" + "deadbeef" * 8)[:64]
    vault_file = os.path.join(data_dir, hash_id)
    with open(vault_file, "wb") as f:
        f.write(b"fake")
    src_abs = os.path.join(books_dir, src_rel)
    os.makedirs(os.path.dirname(src_abs), exist_ok=True)
    os.symlink(os.path.relpath(vault_file, os.path.dirname(src_abs)), src_abs)

    db = TestSession()
    try:
        db.add(models.Book(
            id=hash_id, title="Армяно-русский словарь",
            original_filename=os.path.basename(src_rel),
            import_date=datetime.now(timezone.utc).isoformat(), clearance=clearance,
        ))
        db.add(models.BookLocation(hash_id=hash_id, symlink_path=src_rel))
        db.commit()
    finally:
        db.close()
    return hash_id


def _page_count(TestSession, models):
    db = TestSession()
    try:
        return db.query(models.UsageEvent).filter(models.UsageEvent.kind == "page").count()
    finally:
        db.close()


def test_item_returns_metadata_without_page_event(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"))
    hash_id = _make_book(tmp_path, monkeypatch, helpers)

    from fastapi.testclient import TestClient
    c = TestClient(main.app)

    before = _page_count(TestSession, models)
    r = c.get("/api/item", params={"path": "Dictionaries/Armenian/slovar.djvu"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "slovar.djvu"
    assert body["hash_id"] == hash_id
    assert body["is_dir"] is False
    # Opening a book is not directory navigation — it logs no page event.
    assert _page_count(TestSession, models) == before


def test_item_404_for_missing_file(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"))
    _make_book(tmp_path, monkeypatch, helpers)

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.get("/api/item", params={"path": "Dictionaries/Armenian/nope.djvu"})
    assert r.status_code == 404


def test_browse_parent_still_logs_page(app_ctx, tmp_path, monkeypatch):
    """Sanity contrast: browsing the parent directory DOES record one page event,
    so the no-event assertion above is about /api/item, not a dead test."""
    helpers, _captured, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"))
    _make_book(tmp_path, monkeypatch, helpers)

    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    before = _page_count(TestSession, models)
    r = c.get("/api/browse", params={"path": "Dictionaries/Armenian"})
    assert r.status_code == 200, r.text
    assert _page_count(TestSession, models) == before + 1
