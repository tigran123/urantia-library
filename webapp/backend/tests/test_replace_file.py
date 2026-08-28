"""Coverage for the admin replace-file flow: swapping a book's bytes while
keeping its identity.

`books.id` IS the BLAKE2b hash of the file, so corrected bytes necessarily mean
a new primary key. What these tests actually protect is that the re-key carries
every reference across — a naive delete-and-reinsert would cascade away every
rating, comment, annotation, reading position and playlist entry, silently.

Reuses test_upload_commit's `upload_ctx` harness (BOOKS_DIR/DATA_DIR/
STAGING_DIR/COVERS_DIR redirected to tmp, admin client ready) and its
upload/commit helpers, since a book must exist before it can be replaced.
"""
from __future__ import annotations

import json
import os

import pytest

from .test_upload_commit import (            # noqa: F401 — upload_ctx is a fixture
    upload_ctx, upload, parse_done, commit, FB2_XML,
)

# Distinct bytes → distinct hash. Stands in for "same book, corrected file".
FB2_FIXED = FB2_XML.replace(b"Hello world", b"Hello world, with a fixed outline")


def stage(client, filename: str, raw: bytes) -> dict:
    done = parse_done(upload(client, filename, raw))
    assert "error" not in done and "existing" not in done, done
    return done


def make_book(client, filename="book.fb2", raw=FB2_XML, **kw) -> dict:
    """Upload + commit one book, returning its AdminBookDetail."""
    res = commit(client, stage(client, filename, raw)["staging_id"], **kw)
    assert res.status_code == 200, res.text
    return res.json()


def replace(client, hash_id: str, staging_id: str):
    return client.post(f"/api/admin/books/{hash_id}/replace",
                       json={"staging_id": staging_id})


# ---- the guard ------------------------------------------------------------

def test_every_books_fk_is_covered(upload_ctx):
    """_rekey_book's final statement is `DELETE FROM books`, which cascades away
    any child row that was not moved first. So the list of book-referencing
    columns must stay complete: this fails the day an eleventh table is added
    without updating _BOOK_HASH_REFS."""
    helpers, _client, _b, _d, main = upload_ctx
    db = helpers["TestSession"]()
    try:
        discovered = main._discover_book_hash_refs(db)
    finally:
        db.close()
    assert discovered == set(main._BOOK_HASH_REFS), (
        "schema and _BOOK_HASH_REFS disagree; symmetric difference: "
        f"{discovered ^ set(main._BOOK_HASH_REFS)}"
    )
    # Sanity: the oracle actually found something, so an empty-set match can't
    # pass this test vacuously.
    assert ("book_locations", "hash_id") in discovered


# ---- the point of the whole feature ---------------------------------------

def test_replace_preserves_user_state(upload_ctx):
    helpers, client, _b, _d, main = upload_ctx
    models = helpers["models"]
    book = make_book(client, metadata={"title": "Corrected Book", "author": "de Broglie"})
    old_hash = book["id"]

    reader = helpers["make_user"]("reader@example.com")
    db = helpers["TestSession"]()
    try:
        now = main._now_iso()
        db.add(models.BookRating(user_id=reader, hash_id=old_hash, rating=5,
                                 created_at=now, updated_at=now))
        db.add(models.BookComment(user_id=reader, hash_id=old_hash, body="Great",
                                  status="approved", created_at=now, updated_at=now))
        db.add(models.Annotation(user_id=reader, hash_id=old_hash,
                                 anchor=json.dumps({"format": "pdf", "page": 3}),
                                 selected_text="wave", created_at=now, updated_at=now))
        db.add(models.ReadingProgress(user_id=reader, hash_id=old_hash,
                                      location="42", percent=0.5))
        db.add(models.Favorite(user_id=reader, hash_id=old_hash))
        db.add(models.BookRecommendation(hash_id=old_hash, recommended_by=None,
                                         recommended_at=now))
        db.add(models.UsageEvent(ts=now, user_id=reader, ip="127.0.0.1",
                                 kind="book_open", hash_id=old_hash))
        pl = models.Playlist(owner_id=reader, name="Physics", kind="custom",
                             visibility="private", created_at=now, updated_at=now)
        db.add(pl); db.flush()
        db.add(models.PlaylistItem(playlist_id=pl.id, item_type="book",
                                   book_hash_id=old_hash, position=0, added_at=now))
        db.commit()
    finally:
        db.close()

    res = replace(client, old_hash, stage(client, "book.fb2", FB2_FIXED)["staging_id"])
    assert res.status_code == 200, res.text
    new_hash = res.json()["id"]
    assert new_hash != old_hash

    # Curated metadata is the whole reason not to delete-and-reupload.
    assert res.json()["title"] == "Corrected Book"
    assert res.json()["author"] == "de Broglie"

    db = helpers["TestSession"]()
    try:
        assert db.query(models.Book).count() == 1
        assert db.query(models.Book).filter_by(id=old_hash).first() is None
        for model, col in [
            (models.BookLocation, "hash_id"), (models.Favorite, "hash_id"),
            (models.ReadingProgress, "hash_id"), (models.BookRating, "hash_id"),
            (models.BookComment, "hash_id"), (models.Annotation, "hash_id"),
            (models.BookRecommendation, "hash_id"), (models.UsageEvent, "hash_id"),
            (models.PlaylistItem, "book_hash_id"),
        ]:
            moved = db.query(model).filter(getattr(model, col) == new_hash).count()
            stale = db.query(model).filter(getattr(model, col) == old_hash).count()
            assert moved >= 1, f"{model.__tablename__}.{col} lost its row"
            assert stale == 0, f"{model.__tablename__}.{col} still points at the old hash"
        # Payload survived the move, not just the row count.
        assert db.query(models.BookRating).filter_by(hash_id=new_hash).one().rating == 5
        assert db.query(models.ReadingProgress).filter_by(hash_id=new_hash).one().location == "42"
    finally:
        db.close()


def test_replace_rewrites_symlinks_and_swaps_vault(upload_ctx):
    helpers, client, books, data, _m = upload_ctx
    book = make_book(client, top_dir="Science", subpath="deBroglie")
    old_hash = book["id"]
    # A second location for the same book — the Recommended/ link takes this
    # shape, and every registered location must be re-pointed, not just the first.
    extra = books / "Recommended" / "Alias.fb2.zip"
    extra.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.relpath(data / old_hash, extra.parent), extra)
    db = helpers["TestSession"]()
    try:
        db.add(helpers["models"].BookLocation(
            hash_id=old_hash, symlink_path="Recommended/Alias.fb2.zip"))
        db.commit()
    finally:
        db.close()

    res = replace(client, old_hash, stage(client, "book.fb2", FB2_FIXED)["staging_id"])
    assert res.status_code == 200, res.text
    new_hash = res.json()["id"]

    assert os.path.exists(data / new_hash)
    assert not os.path.exists(data / old_hash), "superseded vault file was left behind"
    for loc in ("Science/deBroglie/book.fb2.zip", "Recommended/Alias.fb2.zip"):
        link = books / loc
        assert os.path.islink(link), f"{loc} is no longer a symlink"
        assert os.path.realpath(link) == os.path.realpath(data / new_hash), \
            f"{loc} still resolves to the old bytes"
        assert not os.path.isabs(os.readlink(link)), "link target must stay relative"
    # The paths themselves are untouched — that is what keeps /item/<path>
    # bookmarks and share links working across a replace.
    assert set(res.json()["locations"]) == {
        "Science/deBroglie/book.fb2.zip", "Recommended/Alias.fb2.zip"}


def test_replace_carries_the_cover_over(upload_ctx):
    helpers, client, _b, data, _m = upload_ctx
    book = make_book(client)
    old_hash = book["id"]
    covers = data / "covers"
    covers.mkdir(parents=True, exist_ok=True)
    (covers / f"{old_hash}.jpg").write_bytes(b"hand-picked cover")

    res = replace(client, old_hash, stage(client, "book.fb2", FB2_FIXED)["staging_id"])
    assert res.status_code == 200, res.text
    new_hash = res.json()["id"]
    # A hand-uploaded cover can't be re-derived, so it follows the book rather
    # than being replaced by whatever the new file yields.
    assert (covers / f"{new_hash}.jpg").read_bytes() == b"hand-picked cover"
    assert not (covers / f"{old_hash}.jpg").exists()
    assert res.json()["cover_url"].startswith(f"/api/covers/{new_hash}")


def test_replace_clears_last_verified(upload_ctx):
    helpers, client, _b, _d, _m = upload_ctx
    models = helpers["models"]
    old_hash = make_book(client)["id"]
    db = helpers["TestSession"]()
    try:
        b = db.query(models.Book).filter_by(id=old_hash).one()
        b.last_verified_at, b.last_verified_ok = "2026-01-01T00:00:00Z", True
        b.last_verified_mode, b.last_verified_error = "full", None
        db.commit()
    finally:
        db.close()

    res = replace(client, old_hash, stage(client, "book.fb2", FB2_FIXED)["staging_id"])
    assert res.status_code == 200, res.text
    db = helpers["TestSession"]()
    try:
        b = db.query(models.Book).filter_by(id=res.json()["id"]).one()
        # Carrying the old verdict forward would have Admin -> Integrity report
        # a full scan that never ran against these bytes.
        assert b.last_verified_at is None and b.last_verified_ok is None
        assert b.last_verified_mode is None and b.last_verified_error is None
    finally:
        db.close()


def test_replace_writes_an_audit_row(upload_ctx):
    helpers, client, _b, _d, _m = upload_ctx
    models = helpers["models"]
    old_hash = make_book(client, metadata={"title": "Revolution"})["id"]
    res = replace(client, old_hash, stage(client, "book.fb2", FB2_FIXED)["staging_id"])
    assert res.status_code == 200, res.text

    db = helpers["TestSession"]()
    try:
        row = db.query(models.AdminAuditLog).filter_by(action="book.replace").one()
        assert row.target_kind == "book" and row.target_id == res.json()["id"]
        details = json.loads(row.details_json)
        assert details["old_hash"] == old_hash
        assert details["new_hash"] == res.json()["id"]
        assert details["title"] == "Revolution"
    finally:
        db.close()


# ---- refusals -------------------------------------------------------------

def test_replace_rejects_extension_change(upload_ctx):
    _h, client, _b, _d, _m = upload_ctx
    old_hash = make_book(client)["id"]                     # .fb2 -> .fb2.zip
    sid = stage(client, "fixed.html",
                b"<!doctype html><html><body><h1>Hi</h1></body></html>")["staging_id"]
    res = replace(client, old_hash, sid)
    assert res.status_code == 400
    assert ".fb2.zip" in res.json()["detail"]


def test_replace_rejects_a_hash_owned_by_another_book(upload_ctx):
    _h, client, _b, _d, main = upload_ctx
    first = make_book(client, "one.fb2", FB2_XML, top_dir="A")["id"]
    second = make_book(client, "two.fb2", FB2_FIXED, top_dir="B",
                       metadata={"title": "Other"})["id"]
    # Staging refuses a known hash outright, so the only way to reach this guard
    # is the race it exists for: another admin commits that hash between staging
    # and replace. Retarget the staged record to simulate it.
    done = stage(client, "three.fb2", FB2_XML.replace(b"Hello", b"Bonjour"))
    main._STAGING[done["staging_id"]]["hash"] = second

    res = replace(client, first, done["staging_id"])
    assert res.status_code == 409
    assert "Other" in res.json()["detail"]


def test_replace_of_unknown_book_is_404(upload_ctx):
    _h, client, _b, _d, _m = upload_ctx
    sid = stage(client, "book.fb2", FB2_XML)["staging_id"]
    assert replace(client, "0" * 128, sid).status_code == 404


def test_replace_staging_is_owner_scoped(upload_ctx):
    helpers, client, _b, _d, _m = upload_ctx
    old_hash = make_book(client)["id"]
    helpers["make_user"]("admin2@example.com", admin=True)
    other = helpers["client_for"]("admin2@example.com")
    sid = stage(other, "book.fb2", FB2_FIXED)["staging_id"]
    # Possession of an unguessable staging_id must not let another admin spend
    # someone else's in-flight upload — same rule the commit path enforces.
    res = replace(client, old_hash, sid)
    assert res.status_code == 410


def test_failed_replace_leaves_the_book_intact(upload_ctx, monkeypatch):
    """A DB failure after the filesystem work must roll the filesystem back:
    old bytes still in the vault, symlinks still resolving, book still there."""
    helpers, client, books, data, main = upload_ctx
    old_hash = make_book(client)["id"]
    sid = stage(client, "book.fb2", FB2_FIXED)["staging_id"]

    import routers.admin_uploads as au
    def boom(db, old, new):
        raise RuntimeError("simulated re-key failure")
    monkeypatch.setattr(au, "_rekey_book", boom)

    res = replace(client, old_hash, sid)
    assert res.status_code == 500

    link = books / "Test" / "book.fb2.zip"
    assert os.path.exists(data / old_hash)
    assert os.path.realpath(link) == os.path.realpath(data / old_hash)
    db = helpers["TestSession"]()
    try:
        assert db.query(helpers["models"].Book).filter_by(id=old_hash).first() is not None
        assert db.query(helpers["models"].Book).count() == 1
    finally:
        db.close()
