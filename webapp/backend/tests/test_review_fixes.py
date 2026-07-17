"""Regression coverage for the external-review fixes:

- Admin filesystem mutations (commit / move / dir delete / book delete) refuse
  paths whose directory chain runs through a symlink escaping BOOKS_DIR or
  resolving into infra under it (`_assert_mutation_path` in paths.py).
- Upload commit is compensated on DB failure: no orphan vault/cover files, the
  staging record stays valid, and the commit can simply be retried.
- Single-book delete removes the vault bytes only AFTER the DB commit.
- POST /api/progress 404s on an unknown hash instead of tripping the FK (500).
- An internal admin note that also flips the thread status still sends the
  status email (only the reply email is suppressed for internal notes).
- Staging records are owner-scoped: possession of another admin's staging_id
  grants nothing.
"""
from __future__ import annotations

import os

import pytest

from .test_upload_commit import FB2_XML, _write, upload, parse_done, commit, upload_ctx  # noqa: F401


def _mk_book(TestSession, models, hash_id: str, path: str | None = None, clearance: int = 0):
    """Insert a Book (+ optional BookLocation) row directly — no files needed."""
    db = TestSession()
    try:
        db.add(models.Book(
            id=hash_id, title=f"B {hash_id[:8]}", original_filename="b.pdf",
            clearance=clearance, import_date="2026-01-01T00:00:00Z",
        ))
        if path:
            db.add(models.BookLocation(hash_id=hash_id, symlink_path=path))
        db.commit()
    finally:
        db.close()


# ---- symlinked-directory escape refusal (finding: lexical-only checks) -------

def test_commit_through_symlinked_dir_403(upload_ctx):
    _h, client, books, data, main = upload_ctx
    outside = os.path.join(os.path.dirname(str(books)), "outside-commit")
    os.makedirs(outside, exist_ok=True)
    os.symlink(outside, os.path.join(str(books), "Escape"))

    done = parse_done(upload(client, "book.fb2", FB2_XML))
    res = commit(client, done["staging_id"], top_dir="Escape")
    assert res.status_code == 403, res.text
    assert os.listdir(outside) == []                     # nothing landed outside
    assert not os.path.exists(os.path.join(str(data), done["hash"]))  # vault untouched
    assert done["staging_id"] in main._STAGING           # staging intact — retryable


def test_commit_into_infra_symlink_403(upload_ctx):
    # A benign-looking top dir that resolves INTO .data (infra under BOOKS_DIR).
    _h, client, books, data, main = upload_ctx
    os.symlink(os.path.join(str(data), "covers"), os.path.join(str(books), "Covers"))
    done = parse_done(upload(client, "book.fb2", FB2_XML))
    res = commit(client, done["staging_id"], top_dir="Covers")
    assert res.status_code == 403, res.text
    assert os.listdir(os.path.join(str(data), "covers")) == []


def test_move_dst_through_symlinked_dir_refused(upload_ctx):
    helpers, client, books, _d, main = upload_ctx
    done = parse_done(upload(client, "book.fb2", FB2_XML))
    assert commit(client, done["staging_id"]).status_code == 200   # → Test/book.fb2.zip

    outside = os.path.join(os.path.dirname(str(books)), "outside-move")
    os.makedirs(outside, exist_ok=True)
    os.symlink(outside, os.path.join(str(books), "EscapeDir"))

    res = client.post("/api/admin/move", json={
        "src": "Test/book.fb2.zip", "dst": "EscapeDir/book.fb2.zip",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moved"] == []
    assert any("refused traversal" in e["reason"] for e in body["errors"]), body
    assert os.listdir(outside) == []
    assert os.path.islink(books / "Test" / "book.fb2.zip")   # source untouched

    models = helpers["models"]
    db = helpers["TestSession"]()
    try:
        assert db.query(models.BookLocation).filter_by(
            symlink_path="Test/book.fb2.zip").first() is not None
    finally:
        db.close()


def test_dir_delete_through_symlinked_dir_403(upload_ctx):
    _h, client, books, _d, _m = upload_ctx
    outside = os.path.join(os.path.dirname(str(books)), "outside-del")
    _write(os.path.join(outside, "sub", "victim.txt"), b"precious")
    os.makedirs(os.path.join(str(books), "TopicX"), exist_ok=True)
    os.symlink(outside, os.path.join(str(books), "TopicX", "escape"))

    # Leaf symlink itself: rejected by the existing islink gate.
    res = client.delete("/api/admin/dirs", params={"path": "TopicX/escape"})
    assert res.status_code == 400, res.text
    # Intermediate symlink component: leaf is a real (outside) dir — only the
    # realpath guard catches this one.
    res = client.delete("/api/admin/dirs", params={"path": "TopicX/escape/sub"})
    assert res.status_code == 403, res.text
    assert os.path.exists(os.path.join(outside, "sub", "victim.txt"))


def test_book_delete_refuses_escaping_location(upload_ctx):
    helpers, client, books, data, _m = upload_ctx
    done = parse_done(upload(client, "book.fb2", FB2_XML))
    assert commit(client, done["staging_id"]).status_code == 200
    hash_id = done["hash"]

    outside = os.path.join(os.path.dirname(str(books)), "outside-bookdel")
    os.makedirs(outside, exist_ok=True)
    victim = os.path.join(outside, "book.fb2.zip")
    os.symlink("/nonexistent-target", victim)            # symlink outside the tree
    os.makedirs(os.path.join(str(books), "Evil"), exist_ok=True)
    os.symlink(outside, os.path.join(str(books), "Evil", "escape"))

    models = helpers["models"]
    db = helpers["TestSession"]()
    try:
        db.add(models.BookLocation(hash_id=hash_id, symlink_path="Evil/escape/book.fb2.zip"))
        db.commit()
    finally:
        db.close()

    res = client.delete(f"/api/admin/books/{hash_id}")
    assert res.status_code == 200, res.text
    assert any("refused traversal" in e for e in res.json()["errors"]), res.json()
    assert os.path.lexists(victim)                        # outside symlink survived
    assert not os.path.exists(os.path.join(str(data), hash_id))  # vault still cleaned


# ---- FS/DB consistency on failure --------------------------------------------

def test_commit_db_failure_leaves_no_orphans_and_is_retryable(upload_ctx):
    helpers, client, books, data, main = upload_ctx
    models = helpers["models"]
    # Occupy the destination symlink_path in the DB only (no file on disk), so
    # the commit passes the lexists check and dies on the PK at db.commit().
    _mk_book(helpers["TestSession"], models, "otherhash", path="Test/book.fb2.zip")

    done = parse_done(upload(client, "book.fb2", FB2_XML))
    sid = done["staging_id"]
    res = commit(client, sid)
    assert res.status_code == 500, res.text
    assert "DB commit failed" in res.json()["detail"]

    # Nothing orphaned: no vault file, no symlink, staging restored intact.
    assert not os.path.exists(os.path.join(str(data), done["hash"]))
    assert not os.path.lexists(books / "Test" / "book.fb2.zip")
    rec = main._STAGING.get(sid)
    assert rec is not None
    assert os.path.isfile(os.path.join(rec["dir"], rec["filename"]))

    # Clear the conflict and retry the SAME staging record — must now succeed.
    db = helpers["TestSession"]()
    try:
        db.query(models.BookLocation).filter_by(symlink_path="Test/book.fb2.zip").delete()
        db.commit()
    finally:
        db.close()
    res = commit(client, sid)
    assert res.status_code == 200, res.text
    assert os.path.exists(os.path.join(str(data), done["hash"]))
    assert os.path.islink(books / "Test" / "book.fb2.zip")


def test_book_delete_keeps_vault_until_commit(upload_ctx, monkeypatch):
    helpers, client, books, data, _m = upload_ctx
    done = parse_done(upload(client, "book.fb2", FB2_XML))
    assert commit(client, done["staging_id"]).status_code == 200
    hash_id = done["hash"]
    vault_file = os.path.join(str(data), hash_id)
    assert os.path.exists(vault_file)

    # Blow up between the ORM deletes and db.commit(): the vault bytes must
    # still be on disk afterwards (they are only removed post-commit now).
    import routers.admin_books as ab

    def boom(*a, **k):
        raise RuntimeError("audit exploded")

    with monkeypatch.context() as m:
        m.setattr(ab, "_audit", boom)
        with pytest.raises(RuntimeError):
            client.delete(f"/api/admin/books/{hash_id}")

    assert os.path.exists(vault_file)                     # vault survived the failure
    models = helpers["models"]
    db = helpers["TestSession"]()
    try:
        assert db.query(models.Book).filter_by(id=hash_id).first() is not None
    finally:
        db.close()

    # Retry without the fault → full cleanup.
    res = client.delete(f"/api/admin/books/{hash_id}")
    assert res.status_code == 200, res.text
    assert not os.path.exists(vault_file)


# ---- POST /api/progress on unknown hash ---------------------------------------

def test_progress_unknown_hash_404(app_ctx):
    helpers, _captured, TestSession = app_ctx
    models = helpers["models"]
    helpers["make_user"]("u@x.com")
    uc = helpers["client_for"]("u@x.com")

    # Unknown hash: clean 404, not an FK IntegrityError 500.
    res = uc.post("/api/progress", json={"hash_id": "nosuchhash", "location": "p1", "percent": 0.5})
    assert res.status_code == 404, res.text

    # Guest no-op unchanged.
    from fastapi.testclient import TestClient
    guest = TestClient(helpers["main"].app)
    res = guest.post("/api/progress", json={"hash_id": "nosuchhash", "location": "p1"})
    assert res.status_code == 200, res.text
    assert res.json()["user_id"] == 0

    # Known book still saves.
    _mk_book(TestSession, models, "knownhash")
    res = uc.post("/api/progress", json={"hash_id": "knownhash", "location": "p2", "percent": 0.25})
    assert res.status_code == 200, res.text
    db = TestSession()
    try:
        row = db.query(models.ReadingProgress).filter_by(hash_id="knownhash").one()
        assert row.location == "p2"
    finally:
        db.close()


# ---- internal note + status flip must still email the status change ----------

def test_internal_note_with_status_sends_status_email(app_ctx, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    helpers["make_user"]("u@x.com")
    helpers["make_user"]("admin@x.com", admin=True)
    uc = helpers["client_for"]("u@x.com")
    ac = helpers["client_for"]("admin@x.com")

    tid = uc.post("/api/feedback", json={
        "category": "general", "subject": "S", "body": "B",
    }).json()["id"]

    # Record notification intents synchronously (the real helper fires a
    # daemon SMTP thread — racy to assert on).
    import routers.feedback as fb
    calls: list[str] = []
    monkeypatch.setattr(fb, "_maybe_email_user_update",
                        lambda thread, db, change: calls.append(change))

    # Internal note that also resolves the thread → status email only.
    r = ac.post(f"/api/feedback/{tid}/reply",
                json={"body": "internal note", "internal": True, "new_status": "resolved"})
    assert r.status_code == 200, r.text
    assert calls == ["status"], calls

    # Plain admin reply → reply email; no status change, no status email.
    calls.clear()
    r = ac.post(f"/api/feedback/{tid}/reply", json={"body": "visible reply"})
    assert r.status_code == 200, r.text
    assert calls == ["reply"], calls

    # Internal note without a status change → no email at all.
    calls.clear()
    r = ac.post(f"/api/feedback/{tid}/reply", json={"body": "another note", "internal": True})
    assert r.status_code == 200, r.text
    assert calls == [], calls


# ---- staging records are owner-scoped -----------------------------------------

def test_staging_owner_scoped(upload_ctx):
    helpers, client_a, _b, _d, main = upload_ctx
    helpers["make_user"]("admin2@example.com", admin=True)
    client_b = helpers["client_for"]("admin2@example.com")

    sid = parse_done(upload(client_a, "book.fb2", FB2_XML))["staging_id"]

    # Admin B holds the id but owns nothing: every staging surface says 404
    # (commit keeps its 410), and the record is untouched.
    assert client_b.get(f"/api/admin/books/upload/{sid}/file").status_code == 404
    assert client_b.get(f"/api/admin/books/upload/{sid}/fb2-content").status_code == 404
    assert client_b.get(f"/api/admin/books/upload/{sid}/cover.jpg").status_code == 404
    assert client_b.delete(f"/api/admin/books/upload/{sid}").status_code == 404
    assert sid in main._STAGING                           # cancel refused, not swallowed
    assert commit(client_b, sid).status_code == 410

    # The owner is unaffected.
    assert client_a.get(f"/api/admin/books/upload/{sid}/file").status_code == 200
    assert client_a.delete(f"/api/admin/books/upload/{sid}").status_code == 200
    assert sid not in main._STAGING
