"""Smoke tests for the admin file-relocation endpoint (POST /api/admin/move).

Covers the two behaviours that matter for the Edit-metadata "Move or rename"
feature added in commit 2b22dd1:

  1. A managed-book symlink can be moved to a nested destination — the
     `book_locations.symlink_path` row is updated, the on-disk symlink is
     rewritten with a relative target into the same vault hash, and the old
     symlink is removed.

  2. A `dst` (or `src`) that escapes BOOKS_DIR is rejected with 400 before
     any filesystem work happens. Pins the `_rel_under_books` guard.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


def _setup_managed_book(tmp_path, monkeypatch, helpers, src_rel: str):
    """Point main.BOOKS_DIR + main.DATA_DIR at tmp_path, create a fake vault
    file + a symlink at src_rel, and insert matching books/book_locations rows.
    Returns (hash_id, vault_file_path)."""
    main = helpers["main"]
    models = helpers["models"]
    TestSession = helpers["TestSession"]

    books_dir = str(tmp_path)
    data_dir = os.path.join(books_dir, ".data")
    os.makedirs(data_dir, exist_ok=True)
    monkeypatch.setattr(main, "BOOKS_DIR", books_dir, raising=False)
    monkeypatch.setattr(main, "DATA_DIR", data_dir, raising=False)

    hash_id = "deadbeef" * 8  # 64 hex chars, BLAKE2b-shaped
    vault_file = os.path.join(data_dir, hash_id)
    with open(vault_file, "wb") as f:
        f.write(b"%PDF-1.4\n%fake\n")

    src_abs = os.path.join(books_dir, src_rel)
    os.makedirs(os.path.dirname(src_abs), exist_ok=True)
    os.symlink(os.path.relpath(vault_file, os.path.dirname(src_abs)), src_abs)

    db = TestSession()
    try:
        db.add(models.Book(
            id=hash_id,
            original_filename=os.path.basename(src_rel),
            import_date=datetime.now(timezone.utc).isoformat(),
            clearance=0,
        ))
        db.add(models.BookLocation(hash_id=hash_id, symlink_path=src_rel))
        db.commit()
    finally:
        db.close()

    return hash_id, vault_file


def test_relocate_moves_symlink_and_updates_db(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")

    src = "Topic/old.pdf"
    dst = "Other/Sub/new.pdf"
    hash_id, vault_file = _setup_managed_book(tmp_path, monkeypatch, helpers, src)

    r = ac.post("/api/admin/move", json={"src": src, "dst": dst})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "file"
    assert body["errors"] == []
    assert len(body["moved"]) == 1
    assert body["moved"][0]["hash_id"] == hash_id

    main = helpers["main"]
    new_abs = os.path.join(main.BOOKS_DIR, dst)
    old_abs = os.path.join(main.BOOKS_DIR, src)
    assert os.path.islink(new_abs)
    assert not os.path.lexists(old_abs)
    # Symlink target must resolve back into the vault — same bytes.
    assert os.path.realpath(new_abs) == os.path.realpath(vault_file)

    models = helpers["models"]
    db = TestSession()
    try:
        rows = db.query(models.BookLocation).filter(
            models.BookLocation.hash_id == hash_id
        ).all()
        paths = [r.symlink_path for r in rows]
        assert paths == [dst]
    finally:
        db.close()


def test_relocate_rejects_path_escape(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")

    src = "Topic/old.pdf"
    _setup_managed_book(tmp_path, monkeypatch, helpers, src)

    # dst escapes BOOKS_DIR — must be 400 before any move happens.
    r = ac.post("/api/admin/move", json={"src": src, "dst": "../escape.pdf"})
    assert r.status_code == 400, r.text
    assert "escapes" in r.json()["detail"].lower()

    # Confirm no side-effect: src symlink still exists, DB row untouched.
    main = helpers["main"]
    models = helpers["models"]
    assert os.path.islink(os.path.join(main.BOOKS_DIR, src))
    db = TestSession()
    try:
        rows = db.query(models.BookLocation).all()
        assert [r.symlink_path for r in rows] == [src]
    finally:
        db.close()
