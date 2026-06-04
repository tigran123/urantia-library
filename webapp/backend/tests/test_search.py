"""Search `ext:` filter tests.

Pins the fix where repeated `ext:` tokens OR together (match ANY listed
extension) instead of the old last-wins behaviour. The search endpoint filters
on `book_locations.symlink_path`, so this is pure DB — no on-disk files or
symlinks are needed (the only filesystem touch is a cover `os.path.exists`,
which simply yields cover_url=None when missing).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


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


def _seed_dated(TestSession, models, hash_id: str, symlink_path: str, title: str, import_date: str):
    db = TestSession()
    try:
        db.add(models.Book(
            id=hash_id,
            original_filename=symlink_path.rsplit("/", 1)[-1],
            title=title,
            import_date=import_date,
            clearance=0,
        ))
        db.add(models.BookLocation(hash_id=hash_id, symlink_path=symlink_path))
        db.commit()
    finally:
        db.close()


def _seed_dated_corpus(helpers):
    models = helpers["models"]
    TestSession = helpers["TestSession"]
    now = datetime.now(timezone.utc)
    _seed_dated(TestSession, models, "a" * 64, "Docs/old.pdf", "alpha", (now - timedelta(days=30)).isoformat())
    _seed_dated(TestSession, models, "b" * 64, "Docs/mid.pdf", "alpha", (now - timedelta(days=3)).isoformat())
    _seed_dated(TestSession, models, "c" * 64, "Docs/new.pdf", "alpha", (now - timedelta(hours=1)).isoformat())


def test_sort_import_date_orders_by_recency(app_ctx):
    """`sort=import_date` orders chronologically (newest first on desc, reversed
    on asc) and surfaces import_date on every match."""
    helpers, _captured, _ = app_ctx
    helpers["make_user"]("admin@example.com", admin=True)
    _seed_dated_corpus(helpers)
    c = helpers["client_for"]("admin@example.com")

    r = c.get("/api/search", params={"q": "alpha", "sort": "import_date", "dir": "desc"})
    assert r.status_code == 200
    body = r.json()
    assert [m["path"] for m in body["matches"]] == ["Docs/new.pdf", "Docs/mid.pdf", "Docs/old.pdf"]
    assert all(m.get("import_date") for m in body["matches"])

    r = c.get("/api/search", params={"q": "alpha", "sort": "import_date", "dir": "asc"})
    assert [m["path"] for m in r.json()["matches"]] == ["Docs/old.pdf", "Docs/mid.pdf", "Docs/new.pdf"]


def test_added_filter_restricts_to_window(app_ctx):
    """`added:7d` keeps only books imported within the last 7 days — a filter-only
    query (no free text) still returns the matching set."""
    helpers, _captured, _ = app_ctx
    helpers["make_user"]("admin@example.com", admin=True)
    _seed_dated_corpus(helpers)
    c = helpers["client_for"]("admin@example.com")

    r = c.get("/api/search", params={"q": "added:7d", "sort": "import_date", "dir": "desc"})
    assert r.status_code == 200
    body = r.json()
    assert [m["path"] for m in body["matches"]] == ["Docs/new.pdf", "Docs/mid.pdf"]
    assert body["total"] == 2


def test_added_filter_units_and_invalid(app_ctx):
    """`added:` accepts h/d/w/m/y; seconds/minutes are not units, and an
    unparseable window matches nothing rather than returning the whole library."""
    helpers, _captured, _ = app_ctx
    models = helpers["models"]
    TestSession = helpers["TestSession"]
    helpers["make_user"]("admin@example.com", admin=True)
    now = datetime.now(timezone.utc)
    # Wide margins so the assertions don't straddle calendar-month boundaries.
    _seed_dated(TestSession, models, "a" * 64, "Docs/ancient.pdf", "alpha", (now - timedelta(days=400)).isoformat())
    _seed_dated(TestSession, models, "b" * 64, "Docs/older.pdf", "alpha", (now - timedelta(days=200)).isoformat())
    _seed_dated(TestSession, models, "c" * 64, "Docs/fresh.pdf", "alpha", (now - timedelta(days=2)).isoformat())
    c = helpers["client_for"]("admin@example.com")

    # Years: the last year keeps the 200- and 2-day books, drops the 400-day one.
    r = c.get("/api/search", params={"q": "added:1y", "sort": "import_date"})
    assert {m["path"] for m in r.json()["matches"]} == {"Docs/older.pdf", "Docs/fresh.pdf"}
    # Months: 'm' means months (not minutes) — last month keeps only the 2-day book.
    r = c.get("/api/search", params={"q": "added:1m", "sort": "import_date"})
    assert [m["path"] for m in r.json()["matches"]] == ["Docs/fresh.pdf"]
    # 's' is no longer a unit, and garbage matches nothing rather than everything.
    assert c.get("/api/search", params={"q": "added:30s"}).json()["total"] == 0
    assert c.get("/api/search", params={"q": "added:xyz"}).json()["total"] == 0


def test_multi_location_book_deduped_with_location_list(app_ctx):
    """A book at several symlink paths appears once, is counted once, and lists
    all of its locations; path/name use the alphabetically-first location."""
    helpers, _captured, _ = app_ctx
    models = helpers["models"]
    TestSession = helpers["TestSession"]
    helpers["make_user"]("admin@example.com", admin=True)
    db = TestSession()
    try:
        db.add(models.Book(
            id="e" * 64, original_filename="rev.pdf", title="Revelation",
            import_date=datetime.now(timezone.utc).isoformat(), clearance=0,
        ))
        db.add(models.BookLocation(hash_id="e" * 64, symlink_path="Religions/Urantia/rev.pdf"))
        db.add(models.BookLocation(hash_id="e" * 64, symlink_path="Recommended/rev.pdf"))
        db.commit()
    finally:
        db.close()
    c = helpers["client_for"]("admin@example.com")

    body = c.get("/api/search", params={"q": "Revelation"}).json()
    assert body["total"] == 1
    assert len(body["matches"]) == 1
    m = body["matches"][0]
    assert sorted(loc["parent_dir"] for loc in m["locations"]) == ["Recommended", "Religions/Urantia"]
    assert m["path"] == "Recommended/rev.pdf"  # MIN(symlink_path)


def test_multi_location_path_filter_uses_filtered_representative(app_ctx):
    """Under a `path:` filter the primary path is the MIN over the *matching*
    locations (so it stays inside the filter and matches sort order), while
    `locations` still lists every place the book lives."""
    helpers, _captured, _ = app_ctx
    models = helpers["models"]
    TestSession = helpers["TestSession"]
    helpers["make_user"]("admin@example.com", admin=True)
    db = TestSession()
    try:
        db.add(models.Book(
            id="e" * 64, original_filename="rev.pdf", title="Revelation",
            import_date=datetime.now(timezone.utc).isoformat(), clearance=0,
        ))
        db.add(models.BookLocation(hash_id="e" * 64, symlink_path="Recommended/rev.pdf"))
        db.add(models.BookLocation(hash_id="e" * 64, symlink_path="Religions/Urantia/rev.pdf"))
        db.commit()
    finally:
        db.close()
    c = helpers["client_for"]("admin@example.com")

    m = c.get("/api/search", params={"q": "path:Religions/"}).json()["matches"][0]
    # Global MIN 'Recommended/...' is excluded by the filter, so the primary path
    # is the filtered MIN — inside the path: filter.
    assert m["path"] == "Religions/Urantia/rev.pdf"
    assert m["parent_dir"] == "Religions/Urantia"
    # All locations are still listed regardless of the filter.
    assert sorted(loc["parent_dir"] for loc in m["locations"]) == ["Recommended", "Religions/Urantia"]


def test_added_absolute_date_is_inclusive(app_ctx):
    """`added:YYYY-MM-DD` includes a book imported at exactly that midnight."""
    helpers, _captured, _ = app_ctx
    models = helpers["models"]
    TestSession = helpers["TestSession"]
    helpers["make_user"]("admin@example.com", admin=True)
    _seed_dated(TestSession, models, "a" * 64, "Docs/onmidnight.pdf", "alpha", "2020-01-15T00:00:00+00:00")
    _seed_dated(TestSession, models, "b" * 64, "Docs/justbefore.pdf", "alpha", "2020-01-14T23:59:59+00:00")
    c = helpers["client_for"]("admin@example.com")

    r = c.get("/api/search", params={"q": "added:2020-01-15", "sort": "import_date"})
    assert r.status_code == 200
    assert [m["path"] for m in r.json()["matches"]] == ["Docs/onmidnight.pdf"]


def test_added_absurd_window_matches_all_without_crashing(app_ctx):
    """A window so large it underflows the date range means 'no lower bound'
    (match all), not an uncaught OverflowError/ValueError → HTTP 500."""
    helpers, _captured, _ = app_ctx
    helpers["make_user"]("admin@example.com", admin=True)
    _seed_dated_corpus(helpers)  # 3 books
    c = helpers["client_for"]("admin@example.com")

    for window in ("99999y", "9999999999w", "999999999d", "123456789m"):
        r = c.get("/api/search", params={"q": f"added:{window}"})
        assert r.status_code == 200, window
        assert r.json()["total"] == 3, window
