"""Tests for the directory-aware bulk admin actions.

In Browse "Select mode" an admin can tick directories alongside books. The
Import action already expanded a ticked directory into the files beneath it;
these tests pin the parallel behaviour added to the other bulk actions —
Verify (integrity jobs), Set clearance, and Recommend now accept a `paths`
list and expand each directory into every registered book in its subtree
(recursively), merged with any explicit `hash_ids`.

The expansion reuses `_expand_dirs_to_hash_ids`, which mirrors the prefix-scan
in admin_move (`book_locations.symlink_path LIKE 'Dir/%'`).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


def _setup(tmp_path, monkeypatch, helpers, books):
    """Point main.{BOOKS_DIR,DATA_DIR,RECOMMENDED_DIR} at tmp_path and create a
    fake vault file + topic symlink + books/book_locations rows for each
    (src_rel, title) entry. Returns hash_ids in input order. Mirrors the helper
    in test_recommend.py."""
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


# Two books under Topic/ (one nested in a sub-directory) plus one control book
# under Other/ that a paths=["Topic"] selection must never touch.
_BOOKS = (
    ("Topic/a.pdf", "Alpha"),
    ("Topic/Sub/b.pdf", "Beta"),
    ("Other/c.pdf", "Gamma"),
)


def test_recommend_bulk_expands_directory(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    models = helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    h_alpha, h_beta, h_gamma = _setup(tmp_path, monkeypatch, helpers, _BOOKS)

    r = ac.post("/api/admin/books/recommend/bulk", json={"paths": ["Topic"]})
    assert r.status_code == 200, r.text
    # Both books under Topic/ (incl. the nested one) are recommended; the
    # control book under Other/ is left alone.
    assert r.json() == {"recommended": 2, "unchanged": 0, "errors": []}

    db = TestSession()
    try:
        recommended = {h for (h,) in db.query(models.BookRecommendation.hash_id).all()}
        assert recommended == {h_alpha, h_beta}
        assert h_gamma not in recommended
    finally:
        db.close()


def test_unrecommend_bulk_expands_directory(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    models = helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    h_alpha, h_beta, h_gamma = _setup(tmp_path, monkeypatch, helpers, _BOOKS)

    # Recommend everything first (incl. the control book), then unrecommend only
    # the Topic/ subtree — Gamma under Other/ must stay recommended.
    ac.post("/api/admin/books/recommend/bulk",
            json={"hash_ids": [h_alpha, h_beta, h_gamma]})
    r = ac.post("/api/admin/books/unrecommend/bulk", json={"paths": ["Topic"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"unrecommended": 2, "unchanged": 0, "errors": []}

    db = TestSession()
    try:
        recommended = {h for (h,) in db.query(models.BookRecommendation.hash_id).all()}
        assert recommended == {h_gamma}
    finally:
        db.close()


def test_clearance_bulk_expands_directory(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    models = helpers["models"]
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    h_alpha, h_beta, h_gamma = _setup(tmp_path, monkeypatch, helpers, _BOOKS)

    r = ac.post("/api/admin/books/clearance", json={"paths": ["Topic"], "clearance": 50})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2, "clearance": 50}

    db = TestSession()
    try:
        clearances = {b.id: b.clearance for b in db.query(models.Book).all()}
        assert clearances[h_alpha] == 50
        assert clearances[h_beta] == 50
        assert clearances[h_gamma] == 0     # outside Topic/ — untouched
    finally:
        db.close()


def test_integrity_job_expands_directory(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    _setup(tmp_path, monkeypatch, helpers, _BOOKS)

    r = ac.post("/api/admin/integrity/jobs",
                json={"scope": "hash_ids", "paths": ["Topic"], "mode": "quick"})
    assert r.status_code == 200, r.text
    # The job enumerates exactly the two books beneath Topic/.
    assert r.json()["total"] == 2


def test_paths_and_hash_ids_merge_and_dedup(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    h_alpha, _h_beta, _h_gamma = _setup(tmp_path, monkeypatch, helpers, _BOOKS)

    # Alpha appears both explicitly and via the Topic/ expansion — it must be
    # counted once, so the total is the 2 distinct Topic/ books, not 3.
    r = ac.post("/api/admin/books/recommend/bulk",
                json={"hash_ids": [h_alpha], "paths": ["Topic"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"recommended": 2, "unchanged": 0, "errors": []}


def test_empty_selection_is_a_noop(app_ctx, tmp_path, monkeypatch):
    """No hash_ids and no resolvable paths → the bulk actions short-circuit
    rather than touching every book."""
    helpers, _captured, _TestSession = app_ctx
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    _setup(tmp_path, monkeypatch, helpers, _BOOKS)

    # A directory with no registered books expands to nothing.
    rc = ac.post("/api/admin/books/clearance", json={"paths": ["Nope"], "clearance": 50})
    assert rc.status_code == 200, rc.text
    assert rc.json() == {"updated": 0, "clearance": 50}

    rr = ac.post("/api/admin/books/recommend/bulk", json={"paths": ["Nope"]})
    assert rr.status_code == 200, rr.text
    assert rr.json() == {"recommended": 0, "unchanged": 0, "errors": []}

    # Integrity still requires a non-empty resolved set.
    ri = ac.post("/api/admin/integrity/jobs",
                 json={"scope": "hash_ids", "paths": ["Nope"], "mode": "quick"})
    assert ri.status_code == 400, ri.text
