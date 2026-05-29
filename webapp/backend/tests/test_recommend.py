"""Tests for the admin "recommend a book" feature.

Recommending materialises a symlink under /Books/Recommended/ plus a
book_recommendations row; unrecommending tears both down. The Recommended/
tree is managed *only* through these endpoints — uploads, moves, and directory
deletes must refuse it. These tests pin:

  1. recommend → symlink + book_locations row + book_recommendations row, and
     /api/browse surfaces is_recommended.
  2. unrecommend → all of the above removed.
  3. the move endpoint rejects Recommended/ as src or dst (the guard fires
     before any filesystem work).
  4. /api/admin/dirs (the upload tree picker) excludes Recommended.
  5. bulk recommend / unrecommend round-trip with idempotent counts.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


def _setup(tmp_path, monkeypatch, helpers, *, books=(("Topic/old.pdf", "Test Book"),)):
    """Point main.{BOOKS_DIR,DATA_DIR,RECOMMENDED_DIR} at tmp_path, create a
    fake vault file + topic symlink + books/book_locations rows for each
    (src_rel, title) entry. Returns a list of hash_ids in input order."""
    main = helpers["main"]
    models = helpers["models"]
    TestSession = helpers["TestSession"]

    books_dir = str(tmp_path)
    data_dir = os.path.join(books_dir, ".data")
    recommended_dir = os.path.join(books_dir, "Recommended")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(recommended_dir, exist_ok=True)
    monkeypatch.setattr(main, "BOOKS_DIR", books_dir, raising=False)
    monkeypatch.setattr(main, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(main, "RECOMMENDED_DIR", recommended_dir, raising=False)

    hash_ids = []
    db = TestSession()
    try:
        for i, (src_rel, title) in enumerate(books):
            hash_id = (f"{i:02x}" + "deadbeef" * 8)[:64]
            vault_file = os.path.join(data_dir, hash_id)
            with open(vault_file, "wb") as f:
                f.write(b"%PDF-1.4\n%fake\n")
            src_abs = os.path.join(books_dir, src_rel)
            os.makedirs(os.path.dirname(src_abs), exist_ok=True)
            os.symlink(os.path.relpath(vault_file, os.path.dirname(src_abs)), src_abs)
            db.add(models.Book(
                id=hash_id, title=title,
                original_filename=os.path.basename(src_rel),
                import_date=datetime.now(timezone.utc).isoformat(), clearance=0,
            ))
            db.add(models.BookLocation(hash_id=hash_id, symlink_path=src_rel))
            hash_ids.append(hash_id)
        db.commit()
    finally:
        db.close()
    return hash_ids


def test_recommend_creates_symlink_row_and_browse_flag(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    admin_id = helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    (hash_id,) = _setup(tmp_path, monkeypatch, helpers)

    r = ac.post(f"/api/admin/books/{hash_id}/recommend")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symlink_path"] == "Recommended/Test Book.pdf"
    assert body["recommended_by"] == admin_id

    # On-disk symlink exists under Recommended/ and resolves into the vault.
    link = os.path.join(main.BOOKS_DIR, "Recommended", "Test Book.pdf")
    assert os.path.islink(link)
    assert os.path.realpath(link) == os.path.realpath(os.path.join(main.DATA_DIR, hash_id))

    db = TestSession()
    try:
        rec = db.query(models.BookRecommendation).filter_by(hash_id=hash_id).first()
        assert rec is not None and rec.recommended_by == admin_id
        locs = {l.symlink_path for l in db.query(models.BookLocation).filter_by(hash_id=hash_id)}
        assert locs == {"Topic/old.pdf", "Recommended/Test Book.pdf"}
        # The audit row records the book's real topic path (not the Recommended/
        # symlink) so the audit feed can render the title as a clickable link.
        import json
        rec_audit = (db.query(models.AdminAuditLog)
                     .filter(models.AdminAuditLog.action == "book.recommend").first())
        assert rec_audit is not None
        assert json.loads(rec_audit.details_json).get("path") == "Topic/old.pdf"
    finally:
        db.close()

    # /api/browse surfaces the flag on the topic listing.
    rb = ac.get("/api/browse", params={"path": "Topic"})
    assert rb.status_code == 200, rb.text
    item = next(i for i in rb.json()["items"] if i.get("hash_id") == hash_id)
    assert item["is_recommended"] is True

    # Idempotent: a second recommend is a no-op (no second symlink).
    r2 = ac.post(f"/api/admin/books/{hash_id}/recommend")
    assert r2.status_code == 200
    assert r2.json()["symlink_path"] is None
    db = TestSession()
    try:
        n = db.query(models.BookLocation).filter(
            models.BookLocation.hash_id == hash_id,
            models.BookLocation.symlink_path.like("Recommended/%"),
        ).count()
        assert n == 1
    finally:
        db.close()


def test_unrecommend_removes_symlink_and_row(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    (hash_id,) = _setup(tmp_path, monkeypatch, helpers)

    ac.post(f"/api/admin/books/{hash_id}/recommend")
    link = os.path.join(main.BOOKS_DIR, "Recommended", "Test Book.pdf")
    assert os.path.islink(link)

    r = ac.delete(f"/api/admin/books/{hash_id}/recommend")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == ["Recommended/Test Book.pdf"]
    assert not os.path.lexists(link)

    db = TestSession()
    try:
        assert db.query(models.BookRecommendation).filter_by(hash_id=hash_id).count() == 0
        locs = {l.symlink_path for l in db.query(models.BookLocation).filter_by(hash_id=hash_id)}
        assert locs == {"Topic/old.pdf"}     # topic location survives
    finally:
        db.close()


def test_move_into_or_out_of_recommended_rejected(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    _setup(tmp_path, monkeypatch, helpers)

    # dst under Recommended → 400 (guard fires before the source-exists check).
    r1 = ac.post("/api/admin/move", json={"src": "Topic/old.pdf", "dst": "Recommended/x.pdf"})
    assert r1.status_code == 400, r1.text
    assert "recommend" in r1.json()["detail"].lower()

    # src under Recommended → 400 too.
    r2 = ac.post("/api/admin/move", json={"src": "Recommended/x.pdf", "dst": "Topic/y.pdf"})
    assert r2.status_code == 400, r2.text

    # A normal move is NOT blocked by this guard (reaches the 404 source check).
    r3 = ac.post("/api/admin/move", json={"src": "Nope/missing.pdf", "dst": "Other/z.pdf"})
    assert r3.status_code == 404, r3.text


def test_admin_dirs_excludes_recommended(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    _setup(tmp_path, monkeypatch, helpers)

    r = ac.get("/api/admin/dirs", params={"path": ""})
    assert r.status_code == 200, r.text
    dirs = r.json()["dirs"]
    assert "Topic" in dirs
    assert "Recommended" not in dirs


def test_bulk_recommend_then_unrecommend(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    models = helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    hash_ids = _setup(tmp_path, monkeypatch, helpers, books=(
        ("Topic/a.pdf", "Alpha"), ("Topic/b.pdf", "Beta"),
    ))

    r = ac.post("/api/admin/books/recommend/bulk", json={"hash_ids": hash_ids})
    assert r.status_code == 200, r.text
    assert r.json() == {"recommended": 2, "unchanged": 0, "errors": []}

    # Re-running is idempotent — both already recommended.
    r2 = ac.post("/api/admin/books/recommend/bulk", json={"hash_ids": hash_ids})
    assert r2.json()["recommended"] == 0 and r2.json()["unchanged"] == 2

    ru = ac.post("/api/admin/books/unrecommend/bulk", json={"hash_ids": hash_ids})
    assert ru.status_code == 200, ru.text
    assert ru.json() == {"unrecommended": 2, "unchanged": 0, "errors": []}

    db = TestSession()
    try:
        assert db.query(models.BookRecommendation).count() == 0
        # Bulk audit rows use the base action + a `bulk` flag (matching the
        # book.clearance pattern), NOT a distinct ".bulk" action string.
        actions = {a for (a,) in db.query(models.AdminAuditLog.action).all()}
        assert "book.recommend" in actions and "book.unrecommend" in actions
        assert not any(a.endswith(".bulk") for a in actions)
        import json
        rec_bulk = (db.query(models.AdminAuditLog)
                    .filter(models.AdminAuditLog.action == "book.recommend").first())
        assert json.loads(rec_bulk.details_json).get("bulk") is True
    finally:
        db.close()
