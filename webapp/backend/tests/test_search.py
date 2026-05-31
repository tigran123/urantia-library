"""Search `ext:` filter tests.

Pins the fix where repeated `ext:` tokens OR together (match ANY listed
extension) instead of the old last-wins behaviour. The search endpoint filters
on `book_locations.symlink_path`, so this is pure DB — no on-disk files or
symlinks are needed (the only filesystem touch is a cover `os.path.exists`,
which simply yields cover_url=None when missing).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _seed_book(TestSession, models, hash_id: str, symlink_path: str, title: str):
    db = TestSession()
    try:
        db.add(models.Book(
            id=hash_id,
            original_filename=symlink_path.rsplit("/", 1)[-1],
            title=title,
            import_date=datetime.now(timezone.utc).isoformat(),
            clearance=0,
        ))
        db.add(models.BookLocation(hash_id=hash_id, symlink_path=symlink_path))
        db.commit()
    finally:
        db.close()


def _seed_corpus(helpers):
    models = helpers["models"]
    TestSession = helpers["TestSession"]
    _seed_book(TestSession, models, "a" * 64, "Music/song.mp3", "Song MP3")
    _seed_book(TestSession, models, "b" * 64, "Music/clip.mp4", "Clip MP4")
    _seed_book(TestSession, models, "c" * 64, "Docs/book.pdf", "Book PDF")


def test_repeated_ext_matches_either(app_ctx):
    """`ext:mp4 ext:mp3` returns files of EITHER extension (OR), not last-wins."""
    helpers, _captured, _TestSession = app_ctx
    helpers["make_user"]("admin@example.com", admin=True)
    _seed_corpus(helpers)
    c = helpers["client_for"]("admin@example.com")

    r = c.get("/api/search", params={"q": "ext:mp4 ext:mp3"})
    assert r.status_code == 200
    body = r.json()
    paths = {m["path"] for m in body["matches"]}
    assert paths == {"Music/song.mp3", "Music/clip.mp4"}
    assert body["total"] == 2


def test_quoted_token_is_phrase_not_ext_filter(app_ctx):
    """A fully-quoted `"ext:Strangest"` is a literal phrase (no `field:` prefix
    is captured before the quote), so it must NOT act as an ext filter — it
    matches a title containing that substring regardless of file extension."""
    helpers, _captured, _TestSession = app_ctx
    models = helpers["models"]
    TestSession = helpers["TestSession"]
    helpers["make_user"]("admin@example.com", admin=True)
    _seed_book(TestSession, models, "d" * 64, "Books/dirac.pdf", "0465018277-text:Strangest Man")
    c = helpers["client_for"]("admin@example.com")

    r = c.get("/api/search", params={"q": '"ext:Strangest"'})
    assert r.status_code == 200
    body = r.json()
    paths = {m["path"] for m in body["matches"]}
    assert "Books/dirac.pdf" in paths


def test_single_ext_unchanged(app_ctx):
    """A single `ext:` still filters to exactly that extension."""
    helpers, _captured, _TestSession = app_ctx
    helpers["make_user"]("admin@example.com", admin=True)
    _seed_corpus(helpers)
    c = helpers["client_for"]("admin@example.com")

    r = c.get("/api/search", params={"q": "ext:mp3"})
    assert r.status_code == 200
    body = r.json()
    paths = {m["path"] for m in body["matches"]}
    assert paths == {"Music/song.mp3"}
    assert body["total"] == 1
