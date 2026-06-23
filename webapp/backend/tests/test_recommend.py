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
from sqlalchemy import text as sa_text


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


def test_recommend_preserves_compound_suffix(app_ctx, tmp_path, monkeypatch):
    """Regression: `_recommended_basename` must keep compound format suffixes
    (.fb2.zip, .txt.zip, …) intact. It previously took only the last extension
    (`original_filename.rsplit(".", 1)[-1]`), so a book uploaded as
    `war.fb2.zip` was recommended as `<Title>.zip` — which mis-reads as ZIP
    everywhere format is inferred from the filename (the /Recommended listing
    and its ItemView) and loses the real extension on download. The fix uses
    `_effective_suffix`, which treats those as a single unit."""
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    hash_ids = _setup(tmp_path, monkeypatch, helpers, books=(
        ("Topic/war.fb2.zip", "War and Peace"),
        ("Topic/notes.txt.zip", "Field Notes"),
    ))

    expected = {
        hash_ids[0]: "Recommended/War and Peace.fb2.zip",
        hash_ids[1]: "Recommended/Field Notes.txt.zip",
    }
    for hash_id, want in expected.items():
        r = ac.post(f"/api/admin/books/{hash_id}/recommend")
        assert r.status_code == 200, r.text
        assert r.json()["symlink_path"] == want
        link = os.path.join(main.BOOKS_DIR, want)
        assert os.path.islink(link)
        assert os.path.realpath(link) == os.path.realpath(os.path.join(main.DATA_DIR, hash_id))


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


def test_unrecommend_keeps_state_when_unlink_fails(app_ctx, tmp_path, monkeypatch):
    """When os.unlink raises a non-FileNotFound OSError (PermissionError, RO
    FS, …), `_remove_recommendation` must NOT delete the book_locations row
    and must NOT drop the book_recommendations row. Previously these ran
    unconditionally, leaving an orphan symlink on disk with no DB pointer
    and falsely reporting `removed: [path]` to the caller.

    Symptom of the old bug: the API said the book was unrecommended; the
    symlink file remained under /Books/Recommended/; the next /api/browse
    no longer marked the book as recommended; the next re-recommend picked
    a different basename (`Foo-2.pdf`) because the orphan still occupied
    the original slot."""
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    (hash_id,) = _setup(tmp_path, monkeypatch, helpers)

    r = ac.post(f"/api/admin/books/{hash_id}/recommend")
    assert r.status_code == 200, r.text

    # Sabotage os.unlink so the unrecommend can't actually remove the symlink.
    def fail_unlink(path):
        raise PermissionError(f"simulated lock on {path}")
    monkeypatch.setattr(main.os, "unlink", fail_unlink)

    r2 = ac.delete(f"/api/admin/books/{hash_id}/recommend")
    assert r2.status_code == 200, r2.text
    # The API must NOT claim removal succeeded when unlink failed.
    assert r2.json()["removed"] == []

    db = TestSession()
    try:
        # book_recommendations row still present (would have caused orphan).
        assert db.query(models.BookRecommendation).filter_by(hash_id=hash_id).count() == 1
        # Recommended/* book_locations row still present.
        recs = db.query(models.BookLocation).filter(
            models.BookLocation.hash_id == hash_id,
            models.BookLocation.symlink_path.like("Recommended/%"),
        ).count()
        assert recs == 1, "book_locations row must survive a failed unlink so retry can complete"
    finally:
        db.close()


def test_recommend_returns_409_on_concurrent_unrecommend_race(app_ctx, tmp_path, monkeypatch):
    """admin_recommend_book re-fetches the BookRecommendation row after
    commit. If a concurrent DELETE /recommend lands between our commit and
    the re-read, that row is gone — and the pre-fix code would dereference
    `rec.recommended_by` → AttributeError 500. The fix raises 409 instead.

    To simulate the race deterministically: patch Session.commit so that
    the very first commit (the one inside admin_recommend_book) is followed
    by a side-by-side session that DELETEs the just-committed row. The
    endpoint's subsequent re-fetch then finds None and must 409."""
    helpers, _captured, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    (hash_id,) = _setup(tmp_path, monkeypatch, helpers)

    from sqlalchemy.orm import Session
    original_commit = Session.commit
    fired = {"once": False}
    def commit_then_concurrent_delete(self):
        result = original_commit(self)
        if not fired["once"]:
            fired["once"] = True
            other = TestSession()
            try:
                other.query(models.BookRecommendation).filter_by(hash_id=hash_id).delete()
                other.commit()
            finally:
                other.close()
        return result
    monkeypatch.setattr(Session, "commit", commit_then_concurrent_delete)

    r = ac.post(f"/api/admin/books/{hash_id}/recommend")
    assert r.status_code == 409, r.text


def test_migration_0005_adds_usage_kinds_to_existing_row(app_ctx):
    """Existing prod's app_meta.usage_events_enabled_kinds row was written
    before _USAGE_KINDS_ALL gained `recommend` and `unrecommend`. After
    deploy, _load_enabled_kinds intersects the loaded JSON with the in-code
    tuple, silently dropping the two new kinds. Migration 0005 patches the
    persisted JSON in place. Verify by simulating the pre-migration state,
    invoking the upgrade function, then reading back."""
    helpers, _captured, _TestSession = app_ctx
    import json as _json
    import importlib.util
    import sqlite3
    from pathlib import Path
    from database import engine

    # Seed an OLD-vocabulary row.
    old_vocab = ["page", "book_open", "search", "login", "register"]
    with engine.begin() as conn:
        conn.execute(sa_text(
            "INSERT OR REPLACE INTO app_meta(key, value) "
            "VALUES ('usage_events_enabled_kinds', :v)"
        ), {"v": _json.dumps(old_vocab)})

    # migrate.py uses a raw sqlite3.Connection for .py upgrades — match that.
    mig_path = Path(__file__).resolve().parent.parent / "migrations" / "0005_usage_kinds_add_recommend.py"
    spec = importlib.util.spec_from_file_location("mig0005", mig_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # SQLAlchemy's in-memory StaticPool engine shares one underlying
    # connection — we can extract it via raw_connection().
    raw = engine.raw_connection()
    try:
        mod.upgrade(raw.driver_connection)
        raw.commit()
    finally:
        raw.close()

    with engine.connect() as conn:
        row = conn.execute(sa_text(
            "SELECT value FROM app_meta WHERE key='usage_events_enabled_kinds'"
        )).fetchone()
    after = _json.loads(row[0])
    assert "recommend" in after and "unrecommend" in after
    # Existing entries must be preserved.
    assert set(old_vocab).issubset(after)

    # Idempotent — re-running yields no further change.
    before2 = sorted(after)
    raw = engine.raw_connection()
    try:
        mod.upgrade(raw.driver_connection)
        raw.commit()
    finally:
        raw.close()
    with engine.connect() as conn:
        row2 = conn.execute(sa_text(
            "SELECT value FROM app_meta WHERE key='usage_events_enabled_kinds'"
        )).fetchone()
    assert sorted(_json.loads(row2[0])) == before2
