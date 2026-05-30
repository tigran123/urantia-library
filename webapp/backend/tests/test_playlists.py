"""Tests for the My Playlists feature.

Pins the security-critical behaviours:
  - migration 0006 backfills favorites + directory_favorites into a per-user
    Bookshelf, with directories ordered on top;
  - the Bookshelf is auto-created and not deletable;
  - ownership is enforced server-side on every mutating route, including
    reorder (a recipient of a shared list must not be able to reorder it);
  - the public shared endpoint clearance-filters items and 404s when private;
  - the share token is stable across a private<->public round-trip;
  - "Save a copy" snapshots only the items the caller was allowed to see and is
    owned (and reorderable) by the caller.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _mkbook(db, models, hash_id: str, title: str, clearance: int, path: str | None = None):
    db.add(models.Book(
        id=hash_id, title=title, author="A",
        original_filename=f"{title}.pdf",
        import_date=datetime.now(timezone.utc).isoformat(), clearance=clearance,
    ))
    if path:
        db.add(models.BookLocation(hash_id=hash_id, symlink_path=path))


def _set_clearance(TestSession, models, user_id: int, clearance: int):
    db = TestSession()
    try:
        u = db.query(models.User).filter(models.User.id == user_id).first()
        u.clearance = clearance
        db.commit()
    finally:
        db.close()


def _h(n: int) -> str:
    return (f"{n:02x}" + "abcdef01" * 8)[:64]


# --------------------------------------------------------------------------- #
# 1. Migration backfill (raw sqlite, self-contained)
# --------------------------------------------------------------------------- #
def test_migration_backfills_bookshelf_dirs_on_top():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);
        CREATE TABLE favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT,
            hash_id TEXT, added_date TEXT);
        CREATE TABLE directory_favorites (id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INT, path TEXT, added_date TEXT);
        INSERT INTO users(id, email) VALUES (1, 'a@x.com');
        INSERT INTO favorites(user_id, hash_id, added_date) VALUES
            (1, 'bookB', '2024-01-02'),
            (1, 'bookA', '2024-01-01');
        INSERT INTO directory_favorites(user_id, path, added_date) VALUES
            (1, 'Law', '2024-03-01'),
            (1, 'Grammars', '2024-02-01');
        """
    )
    spec = importlib.util.spec_from_file_location(
        "mig0006", BACKEND_DIR / "migrations" / "0006_playlists.py"
    )
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    mig.upgrade(conn)
    conn.commit()

    shelf = conn.execute(
        "SELECT id, kind, visibility, name FROM playlists WHERE owner_id=1"
    ).fetchone()
    assert shelf is not None
    assert shelf[1] == "bookshelf" and shelf[2] == "private" and shelf[3] == "Bookshelf"

    rows = conn.execute(
        "SELECT item_type, book_hash_id, dir_path FROM playlist_items "
        "WHERE playlist_id=? ORDER BY position", (shelf[0],)
    ).fetchall()
    # Directories first (by added_date: Grammars before Law), then books
    # (by added_date: bookA before bookB).
    assert rows == [
        ("directory", None, "Grammars"),
        ("directory", None, "Law"),
        ("book", "bookA", None),
        ("book", "bookB", None),
    ]
    # Re-running is idempotent — no duplicate bookshelf / items.
    mig.upgrade(conn)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM playlists WHERE owner_id=1").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM playlist_items").fetchone()[0] == 4
    conn.close()


# --------------------------------------------------------------------------- #
# 2. Bookshelf auto-create + undeletable
# --------------------------------------------------------------------------- #
def test_bookshelf_autocreated_and_undeletable(app_ctx):
    helpers, _, _ = app_ctx
    helpers["make_user"]("a@x.com")
    c = helpers["client_for"]("a@x.com")

    r = c.get("/api/playlists")
    assert r.status_code == 200, r.text
    lists = r.json()["items"]
    shelves = [p for p in lists if p["is_bookshelf"]]
    assert len(shelves) == 1
    assert shelves[0]["kind"] == "bookshelf"

    # The Bookshelf cannot be deleted.
    r = c.delete(f"/api/playlists/{shelves[0]['id']}")
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# 2b. Bookshelf race-safety: only one Bookshelf per user even under a
# manual second INSERT that bypasses _get_or_create_bookshelf's pre-check.
# --------------------------------------------------------------------------- #
def test_bookshelf_unique_per_user(app_ctx):
    """Migration 0008 + the partial unique index in models.py guarantee one
    Bookshelf per user. A second INSERT must raise IntegrityError."""
    helpers, _, TestSession = app_ctx
    models = helpers["models"]
    uid = helpers["make_user"]("a@x.com")
    # Trigger the autocreate path once via the normal route.
    helpers["client_for"]("a@x.com").get("/api/playlists")
    from sqlalchemy.exc import IntegrityError as _IE
    db = TestSession()
    try:
        db.add(models.Playlist(
            owner_id=uid, name="Bookshelf", description=None,
            visibility="private", kind="bookshelf",
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        ))
        try:
            db.commit()
            raised = False
        except _IE:
            db.rollback()
            raised = True
        assert raised, "expected IntegrityError from the partial unique index"
        n = db.query(models.Playlist).filter(
            models.Playlist.owner_id == uid,
            models.Playlist.kind == "bookshelf",
        ).count()
        assert n == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 3. Ownership enforcement (incl. owner-only reorder)
# --------------------------------------------------------------------------- #
def test_ownership_and_reorder_enforced(app_ctx):
    helpers, _, TestSession = app_ctx
    models = helpers["models"]
    helpers["make_user"]("owner@x.com")
    helpers["make_user"]("other@x.com")
    owner = helpers["client_for"]("owner@x.com")
    other = helpers["client_for"]("other@x.com")

    db = TestSession()
    try:
        _mkbook(db, models, _h(1), "One", 0, "Topic/one.pdf")
        _mkbook(db, models, _h(2), "Two", 0, "Topic/two.pdf")
        db.commit()
    finally:
        db.close()

    pid = owner.post("/api/playlists", json={"name": "Mine"}).json()["id"]
    i1 = owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)}).json()["id"]
    i2 = owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(2)}).json()["id"]

    # A non-owner cannot view, edit, add to, delete, or reorder it.
    assert other.get(f"/api/playlists/{pid}").status_code == 403
    assert other.patch(f"/api/playlists/{pid}", json={"name": "Hijack"}).status_code == 403
    assert other.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)}).status_code == 403
    assert other.delete(f"/api/playlists/{pid}").status_code == 403
    assert other.put(f"/api/playlists/{pid}/order", json={"item_ids": [i2, i1]}).status_code == 403

    # The owner can reorder.
    r = owner.put(f"/api/playlists/{pid}/order", json={"item_ids": [i2, i1]})
    assert r.status_code == 200, r.text
    order = [it["id"] for it in owner.get(f"/api/playlists/{pid}").json()["items"]]
    assert order == [i2, i1]

    # A bad permutation is rejected.
    assert owner.put(f"/api/playlists/{pid}/order", json={"item_ids": [i1]}).status_code == 400


# --------------------------------------------------------------------------- #
# 3b. Adding the same book/dir twice is idempotent (returns the same row, no
# duplicate, no 500). The endpoint's IntegrityError catch additionally makes
# this hold under a concurrent double-add that races past the existence check;
# that race can't be simulated on the single-connection in-memory test DB, but
# the sequential contract is pinned here.
# --------------------------------------------------------------------------- #
def test_add_item_idempotent(app_ctx):
    helpers, _, TestSession = app_ctx
    models = helpers["models"]
    helpers["make_user"]("owner@x.com")
    owner = helpers["client_for"]("owner@x.com")
    db = TestSession()
    try:
        _mkbook(db, models, _h(1), "One", 0, "Topic/one.pdf")
        db.commit()
    finally:
        db.close()

    pid = owner.post("/api/playlists", json={"name": "Mine"}).json()["id"]
    r1 = owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)})
    r2 = owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    # Same row returned both times, and only one item exists.
    assert r1.json()["id"] == r2.json()["id"]
    items = owner.get(f"/api/playlists/{pid}").json()["items"]
    assert sum(1 for it in items if it.get("hash_id") == _h(1)) == 1


# --------------------------------------------------------------------------- #
# 4. Public shared view: clearance filtering + 404 when private
# --------------------------------------------------------------------------- #
def test_shared_clearance_filtering_and_private_404(app_ctx):
    helpers, _, TestSession = app_ctx
    models = helpers["models"]
    owner_id = helpers["make_user"]("owner@x.com")
    _set_clearance(TestSession, models, owner_id, 100)
    owner = helpers["client_for"]("owner@x.com")

    db = TestSession()
    try:
        _mkbook(db, models, _h(1), "Public", 0, "T/public.pdf")
        _mkbook(db, models, _h(2), "Gated", 50, "T/gated.pdf")
        db.commit()
    finally:
        db.close()

    pid = owner.post("/api/playlists", json={"name": "Shared"}).json()["id"]
    owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)})
    owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(2)})
    token = owner.post(f"/api/playlists/{pid}/share").json()["token"]

    # Anonymous viewer (clearance 0) sees only the public book; the gated one is
    # silently omitted — no count, no placeholder.
    from fastapi.testclient import TestClient
    guest = TestClient(helpers["main"].app)
    r = guest.get(f"/api/shared/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    titles = [it.get("title") for it in body["items"]]
    assert titles == ["Public"]
    assert body["playlist"]["item_count"] == 1
    assert "Gated" not in str(body)
    # Clearance must never be disclosed to non-admins — not even the value/key.
    assert all("clearance" not in it for it in body["items"])
    # is_owner drives the "Back to your playlist" banner; must be false for
    # anonymous viewers (and for non-owner signed-in viewers, by extension).
    assert body["playlist"]["is_owner"] is False
    # The owner previewing their own shared link does see is_owner=True so the
    # public chrome can offer a one-click bridge back to the owner page.
    assert owner.get(f"/api/shared/{token}").json()["playlist"]["is_owner"] is True

    # Going private 404s the shared endpoint.
    owner.delete(f"/api/playlists/{pid}/share")
    assert guest.get(f"/api/shared/{token}").status_code == 404


# --------------------------------------------------------------------------- #
# 5. Stable token across private<->public round-trip
# --------------------------------------------------------------------------- #
def test_share_token_is_stable(app_ctx):
    helpers, _, _ = app_ctx
    helpers["make_user"]("owner@x.com")
    owner = helpers["client_for"]("owner@x.com")
    pid = owner.post("/api/playlists", json={"name": "L"}).json()["id"]

    t1 = owner.post(f"/api/playlists/{pid}/share").json()["token"]
    owner.delete(f"/api/playlists/{pid}/share")
    t2 = owner.post(f"/api/playlists/{pid}/share").json()["token"]
    assert t1 and t1 == t2

    from fastapi.testclient import TestClient
    guest = TestClient(helpers["main"].app)
    assert guest.get(f"/api/shared/{t1}").status_code == 200


# --------------------------------------------------------------------------- #
# 6. Save a copy: visible-only snapshot, owned + reorderable by caller
# --------------------------------------------------------------------------- #
def test_save_a_copy_visible_only(app_ctx):
    helpers, _, TestSession = app_ctx
    models = helpers["models"]
    owner_id = helpers["make_user"]("owner@x.com")
    viewer_id = helpers["make_user"]("viewer@x.com")
    _set_clearance(TestSession, models, owner_id, 100)
    _set_clearance(TestSession, models, viewer_id, 10)
    owner = helpers["client_for"]("owner@x.com")
    viewer = helpers["client_for"]("viewer@x.com")

    db = TestSession()
    try:
        _mkbook(db, models, _h(1), "Public", 0, "T/p.pdf")
        _mkbook(db, models, _h(2), "Gated", 50, "T/g.pdf")
        db.commit()
    finally:
        db.close()

    pid = owner.post("/api/playlists", json={"name": "Src"}).json()["id"]
    owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)})
    owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(2)})
    token = owner.post(f"/api/playlists/{pid}/share").json()["token"]

    r = viewer.post(f"/api/shared/{token}/copy")
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert r.json()["item_count"] == 1  # only the public book was visible to the viewer

    detail = viewer.get(f"/api/playlists/{new_id}")
    assert detail.status_code == 200  # the copy is owned by the viewer
    items = detail.json()["items"]
    assert [it["title"] for it in items] == ["Public"]
    assert detail.json()["playlist"]["visibility"] == "private"
    assert detail.json()["playlist"]["is_bookshelf"] is False

    # The copy is reorderable by its new owner.
    assert viewer.put(
        f"/api/playlists/{new_id}/order", json={"item_ids": [items[0]["id"]]}
    ).status_code == 200


# --------------------------------------------------------------------------- #
# 7. Directories as first-class items + contained-keys / membership
# --------------------------------------------------------------------------- #
def test_directory_items_and_contained_keys(app_ctx, tmp_path, monkeypatch):
    helpers, _, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]

    books_dir = str(tmp_path)
    os.makedirs(os.path.join(books_dir, "Law"), exist_ok=True)
    monkeypatch.setattr(main, "BOOKS_DIR", books_dir, raising=False)

    helpers["make_user"]("a@x.com")
    c = helpers["client_for"]("a@x.com")

    db = TestSession()
    try:
        _mkbook(db, models, _h(1), "Book", 0, "Law/book.pdf")
        db.commit()
    finally:
        db.close()

    pid = c.post("/api/playlists", json={"name": "Mix"}).json()["id"]
    rb = c.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)})
    assert rb.status_code == 200, rb.text
    rd = c.post(f"/api/playlists/{pid}/items", json={"dir_path": "Law"})
    assert rd.status_code == 200, rd.text

    items = c.get(f"/api/playlists/{pid}").json()["items"]
    types = {it["item_type"] for it in items}
    assert types == {"book", "directory"}
    dir_item = next(it for it in items if it["item_type"] == "directory")
    assert dir_item["dir_path"] == "Law" and dir_item["exists"] is True

    keys = c.get("/api/playlists/contained-keys").json()
    assert _h(1) in keys["book_hash_ids"]
    assert "Law" in keys["dir_paths"]

    m = c.get("/api/playlists/membership", params={"dir_path": "Law"}).json()
    assert pid in m["playlist_ids"]

    # Adding the same directory again is idempotent.
    assert c.post(f"/api/playlists/{pid}/items", json={"dir_path": "Law"}).status_code == 200
    assert len(c.get(f"/api/playlists/{pid}").json()["items"]) == 2


# --------------------------------------------------------------------------- #
# 8. Playlist actions land in Admin -> Usage -> Timeline (usage_events)
# --------------------------------------------------------------------------- #
def test_playlist_actions_record_usage_events(app_ctx):
    helpers, _, TestSession = app_ctx
    models = helpers["models"]
    helpers["make_user"]("a@x.com")
    c = helpers["client_for"]("a@x.com")

    db = TestSession()
    try:
        _mkbook(db, models, _h(1), "B", 0, "T/b.pdf")
        db.commit()
    finally:
        db.close()

    pid = c.post("/api/playlists", json={"name": "L"}).json()["id"]      # playlist_create
    item = c.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)}).json()  # playlist_add_item
    c.post(f"/api/playlists/{pid}/share")                                # playlist_visibility
    c.delete(f"/api/playlists/{pid}/items/{item['id']}")                 # playlist_remove_item
    c.delete(f"/api/playlists/{pid}")                                    # playlist_delete

    db = TestSession()
    try:
        kinds = [r[0] for r in db.query(models.UsageEvent.kind).all()]
    finally:
        db.close()
    for k in ("playlist_create", "playlist_add_item", "playlist_visibility",
              "playlist_remove_item", "playlist_delete"):
        assert k in kinds, f"missing {k}: {kinds}"


# --------------------------------------------------------------------------- #
# 9. Deleting a book removes its playlist_items (SQLite FK is OFF — manual)
# --------------------------------------------------------------------------- #
def test_book_delete_removes_playlist_items(app_ctx, tmp_path, monkeypatch):
    helpers, _, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    monkeypatch.setattr(main, "BOOKS_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(main, "DATA_DIR", os.path.join(str(tmp_path), ".data"), raising=False)

    helpers["make_user"]("admin@x.com", admin=True)
    helpers["make_user"]("owner@x.com")
    admin = helpers["client_for"]("admin@x.com")
    owner = helpers["client_for"]("owner@x.com")

    db = TestSession()
    try:
        _mkbook(db, models, _h(1), "Doomed", 0, "Topic/doomed.pdf")
        db.commit()
    finally:
        db.close()

    pid = owner.post("/api/playlists", json={"name": "L"}).json()["id"]
    owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": _h(1)})
    assert len(owner.get(f"/api/playlists/{pid}").json()["items"]) == 1

    assert admin.delete(f"/api/admin/books/{_h(1)}").status_code == 200
    # No stale/ghost item left referencing the deleted book.
    assert owner.get(f"/api/playlists/{pid}").json()["items"] == []
    db = TestSession()
    try:
        assert db.query(models.PlaylistItem).filter_by(book_hash_id=_h(1)).count() == 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 10. Recursive directory delete removes book AND directory playlist_items
# --------------------------------------------------------------------------- #
def test_dir_delete_removes_playlist_items(app_ctx, tmp_path, monkeypatch):
    helpers, _, TestSession = app_ctx
    main, models = helpers["main"], helpers["models"]
    books_dir = str(tmp_path)
    data_dir = os.path.join(books_dir, ".data")
    os.makedirs(os.path.join(books_dir, "Topic"), exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    monkeypatch.setattr(main, "BOOKS_DIR", books_dir, raising=False)
    monkeypatch.setattr(main, "DATA_DIR", data_dir, raising=False)

    hid = _h(2)
    vault = os.path.join(data_dir, hid)
    with open(vault, "wb") as f:
        f.write(b"%PDF-1.4\n")
    src = os.path.join(books_dir, "Topic", "b.pdf")
    os.symlink(os.path.relpath(vault, os.path.dirname(src)), src)

    helpers["make_user"]("admin@x.com", admin=True)
    helpers["make_user"]("owner@x.com")
    admin = helpers["client_for"]("admin@x.com")
    owner = helpers["client_for"]("owner@x.com")

    db = TestSession()
    try:
        _mkbook(db, models, hid, "Inside", 0, "Topic/b.pdf")
        db.commit()
    finally:
        db.close()

    pid = owner.post("/api/playlists", json={"name": "L"}).json()["id"]
    owner.post(f"/api/playlists/{pid}/items", json={"book_hash_id": hid})   # book item under Topic/
    owner.post(f"/api/playlists/{pid}/items", json={"dir_path": "Topic"})   # the directory itself
    assert len(owner.get(f"/api/playlists/{pid}").json()["items"]) == 2

    assert admin.delete("/api/admin/dirs", params={"path": "Topic"}).status_code == 200
    # Both the orphaned book item and the directory item are cleaned up.
    assert owner.get(f"/api/playlists/{pid}").json()["items"] == []
