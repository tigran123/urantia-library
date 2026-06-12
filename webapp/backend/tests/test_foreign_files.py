"""Foreign (unregistered) files are visible/readable to any signed-in user
regardless of clearance, but never to guests — and their reads are logged.

A "foreign" file is anything under BOOKS_DIR with no `books` row: a plain file
(e.g. dropped in /Books/Unsorted to be imported later), a non-vault symlink, or
an orphan vault symlink. Before this change the directory subtree gate hid
all-foreign directories from every non-admin (they have no readable registered
book), so /Books/Unsorted was effectively admin-only.

These tests pin three things:
  - logged-in users can now browse + open foreign files / all-foreign dirs,
  - guests are blocked everywhere (listing AND direct /api/files download),
  - the no-leak invariant still holds: a directory of only clearance-gated
    registered books (no foreign file) stays hidden from low-clearance users.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


def _setup_tree(tmp_path, monkeypatch, helpers):
    """Build a BOOKS_DIR with four shapes and point main.{BOOKS_DIR,DATA_DIR} at it:
      Topic/public.txt  — registered, clearance 0 (public)
      Topic/loose.txt   — foreign, alongside a readable book (mixed dir)
      Unsorted/foreign.txt — foreign, in an all-foreign dir
      Secret/secret.txt — registered, clearance 100, no foreign sibling (gated-only)
    """
    main = helpers["main"]
    models = helpers["models"]
    TestSession = helpers["TestSession"]

    books_dir = str(tmp_path)
    data_dir = os.path.join(books_dir, ".data")
    os.makedirs(data_dir, exist_ok=True)
    monkeypatch.setattr(main, "BOOKS_DIR", books_dir, raising=False)
    monkeypatch.setattr(main, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(main, "_geo_lookup", lambda ip: ("ZZ", "Testville"), raising=False)

    def add_registered(rel, h, clearance):
        vault = os.path.join(data_dir, h)
        with open(vault, "wb") as f:
            f.write(b"book-bytes")
        abs_ = os.path.join(books_dir, rel)
        os.makedirs(os.path.dirname(abs_), exist_ok=True)
        os.symlink(os.path.relpath(vault, os.path.dirname(abs_)), abs_)
        db = TestSession()
        try:
            db.add(models.Book(
                id=h, title="T-" + h[:6],
                original_filename=os.path.basename(rel),
                import_date=datetime.now(timezone.utc).isoformat(),
                clearance=clearance,
            ))
            db.add(models.BookLocation(hash_id=h, symlink_path=rel))
            db.commit()
        finally:
            db.close()

    def add_foreign(rel, content=b"foreign-bytes"):
        abs_ = os.path.join(books_dir, rel)
        os.makedirs(os.path.dirname(abs_), exist_ok=True)
        with open(abs_, "wb") as f:
            f.write(content)

    add_registered("Topic/public.txt", ("a0" + "11" * 31)[:64], 0)
    add_foreign("Topic/loose.txt")
    add_foreign("Unsorted/foreign.txt", b"hello foreign")
    add_registered("Secret/secret.txt", ("b0" + "22" * 31)[:64], 100)
    return books_dir


def _names(resp):
    return {it["name"] for it in resp.json()["items"]}


# --- All-foreign directory (Unsorted): guest blocked, signed-in allowed --------

def test_guest_cannot_see_or_enter_all_foreign_dir(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    _setup_tree(tmp_path, monkeypatch, helpers)

    from fastapi.testclient import TestClient
    guest = TestClient(main.app)

    # Not listed at the root, and direct access 403s — both via the subtree gate.
    assert "Unsorted" not in _names(guest.get("/api/browse", params={"path": ""}))
    assert guest.get("/api/browse", params={"path": "Unsorted"}).status_code == 403
    assert guest.get("/api/item", params={"path": "Unsorted/foreign.txt"}).status_code == 403
    # Direct download is blocked too (assert_can_read_path).
    assert guest.get("/api/files/Unsorted/foreign.txt").status_code == 403


def test_logged_in_user_sees_and_reads_all_foreign_dir(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    _setup_tree(tmp_path, monkeypatch, helpers)
    helpers["make_user"]("reader@example.com")  # non-admin, clearance 0
    c = helpers["client_for"]("reader@example.com")

    assert "Unsorted" in _names(c.get("/api/browse", params={"path": ""}))
    listing = c.get("/api/browse", params={"path": "Unsorted"})
    assert listing.status_code == 200, listing.text
    assert "foreign.txt" in _names(listing)
    # Foreign files stay hash-less so the frontend treats them as plain files.
    item = c.get("/api/item", params={"path": "Unsorted/foreign.txt"})
    assert item.status_code == 200, item.text
    assert "hash_id" not in item.json()
    content = c.get("/api/files/Unsorted/foreign.txt")
    assert content.status_code == 200
    assert content.content == b"hello foreign"


def test_admin_still_sees_all_foreign_dir(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    _setup_tree(tmp_path, monkeypatch, helpers)
    helpers["make_user"]("admin@example.com", admin=True)
    c = helpers["client_for"]("admin@example.com")
    assert "Unsorted" in _names(c.get("/api/browse", params={"path": ""}))
    assert c.get("/api/files/Unsorted/foreign.txt").status_code == 200


# --- Mixed directory: guests see the public book but not the foreign sibling ---

def test_mixed_dir_hides_foreign_from_guest_shows_to_user(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    _setup_tree(tmp_path, monkeypatch, helpers)
    helpers["make_user"]("reader@example.com")

    from fastapi.testclient import TestClient
    guest = TestClient(main.app)
    user = helpers["client_for"]("reader@example.com")

    guest_names = _names(guest.get("/api/browse", params={"path": "Topic"}))
    assert "public.txt" in guest_names and "loose.txt" not in guest_names
    # Guest may still read the registered public book — no regression.
    assert guest.get("/api/files/Topic/public.txt").status_code == 200

    user_names = _names(user.get("/api/browse", params={"path": "Topic"}))
    assert {"public.txt", "loose.txt"} <= user_names


# --- No-leak invariant: a gated-only dir stays hidden from low-clearance users -

def test_gated_only_dir_still_hidden_from_low_clearance_user(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    _setup_tree(tmp_path, monkeypatch, helpers)
    helpers["make_user"]("reader@example.com")  # clearance 0
    c = helpers["client_for"]("reader@example.com")

    # Secret/ has only a clearance-100 registered book and no foreign file, so it
    # must NOT appear and direct access must 403 — even for a signed-in user.
    assert "Secret" not in _names(c.get("/api/browse", params={"path": ""}))
    assert c.get("/api/browse", params={"path": "Secret"}).status_code == 403
    assert c.get("/api/files/Secret/secret.txt").status_code == 403


# --- Usage logging: foreign reads land in the Timeline (hash_id NULL, path set) -

def test_foreign_read_logged_as_book_open(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    _setup_tree(tmp_path, monkeypatch, helpers)
    helpers["make_user"]("reader@example.com")
    c = helpers["client_for"]("reader@example.com")

    assert c.get("/api/files/Unsorted/foreign.txt").status_code == 200

    db = TestSession()
    try:
        ev = (
            db.query(models.UsageEvent)
            .filter(models.UsageEvent.kind == "book_open")
            .order_by(models.UsageEvent.id.desc())
            .first()
        )
    finally:
        db.close()
    assert ev is not None
    assert ev.hash_id is None              # foreign file → no books row
    assert ev.path == "Unsorted/foreign.txt"


def test_orphan_vault_symlink_read_logged(app_ctx, tmp_path, monkeypatch):
    """An orphan vault symlink (resolves into .data/<hash> but has no books row)
    is foreign. Its hash must NOT be written to usage_events.hash_id (a FK to
    books.id) — doing so raised an IntegrityError that the swallowed telemetry
    path turned into a dropped event. The open is logged with hash_id NULL."""
    helpers, _captured, TestSession = app_ctx
    main = helpers["main"]
    models = helpers["models"]
    books_dir = _setup_tree(tmp_path, monkeypatch, helpers)

    # A vault file with NO books/book_locations row, surfaced via a topic symlink.
    orphan = ("c0" + "33" * 31)[:64]
    vault = os.path.join(books_dir, ".data", orphan)
    with open(vault, "wb") as f:
        f.write(b"orphan-bytes")
    link = os.path.join(books_dir, "Topic", "orphan.txt")
    os.symlink(os.path.relpath(vault, os.path.dirname(link)), link)

    helpers["make_user"]("reader@example.com")
    c = helpers["client_for"]("reader@example.com")

    # Sanity: it resolves to a vault hash but has no books row → classified foreign.
    assert main._resolve_vault_hash(link) == orphan
    r = c.get("/api/files/Topic/orphan.txt")
    assert r.status_code == 200, r.text
    assert r.content == b"orphan-bytes"

    db = TestSession()
    try:
        ev = (
            db.query(models.UsageEvent)
            .filter(models.UsageEvent.kind == "book_open",
                    models.UsageEvent.path == "Topic/orphan.txt")
            .first()
        )
    finally:
        db.close()
    assert ev is not None, "orphan-vault open was dropped instead of logged"
    assert ev.hash_id is None  # the dangling hash was nulled, not inserted


# --- Security: the foreign reveal must NOT expose infra dirs or escape the tree-
#
# Revealing foreign-containing directories removed the subtree-gate 403 that
# used to incidentally block non-admins from navigating into infra. The strong
# guard (_safe_under_books: realpath containment + _TOPDIR_SKIPLIST rejection)
# is now load-bearing on /api/browse, /api/files, and every content endpoint.

def test_skiplisted_infra_dirs_blocked_for_everyone(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    books_dir = _setup_tree(tmp_path, monkeypatch, helpers)
    # The repo itself (skiplisted top-level entry), full of foreign files.
    repo = os.path.join(books_dir, "urantia-library", "webapp", "backend")
    os.makedirs(repo, exist_ok=True)
    with open(os.path.join(repo, "security.py"), "w") as f:
        f.write("SECRET = 'do-not-leak'\n")
    helpers["make_user"]("reader@example.com")
    helpers["make_user"]("admin@example.com", admin=True)

    from fastapi.testclient import TestClient
    guest = TestClient(main.app)
    user = helpers["client_for"]("reader@example.com")
    admin = helpers["client_for"]("admin@example.com")

    for client in (guest, user, admin):
        # Hidden from the root listing (entry skiplist) for everyone...
        assert "urantia-library" not in _names(client.get("/api/browse", params={"path": ""}))
        assert ".data" not in _names(client.get("/api/browse", params={"path": ""}))
        # ...and direct navigation / download / content reads all 403 — including
        # for admins, who manage infra over SSH, not the reader UI.
        assert client.get("/api/browse", params={"path": "urantia-library"}).status_code == 403
        assert client.get("/api/browse",
                          params={"path": "urantia-library/webapp/backend"}).status_code == 403
        assert client.get("/api/files/urantia-library/webapp/backend/security.py").status_code == 403
        assert client.get("/api/md-content",
                          params={"path": "urantia-library/webapp/backend/security.py"}).status_code == 403
        # The vault/db directory is skiplisted too — no browsing the CAS vault.
        assert client.get("/api/browse", params={"path": ".data"}).status_code == 403


def test_symlinked_dir_component_escape_blocked(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    books_dir = _setup_tree(tmp_path, monkeypatch, helpers)
    # A directory OUTSIDE the library, reachable only via a symlinked component.
    outside = os.path.join(str(tmp_path.parent), "outside_lib")
    os.makedirs(outside, exist_ok=True)
    with open(os.path.join(outside, "passwd"), "w") as f:
        f.write("root:x:0:0\n")
    os.symlink(outside, os.path.join(books_dir, "Topic", "escape"))  # Topic/escape -> outside
    helpers["make_user"]("reader@example.com")
    user = helpers["client_for"]("reader@example.com")
    # Lexically under BOOKS_DIR, but realpath() resolves outside → 403.
    assert user.get("/api/files/Topic/escape/passwd").status_code == 403
    assert user.get("/api/browse", params={"path": "Topic/escape"}).status_code == 403
    # The foreign reveal must not even surface the escaping dir's name in Topic.
    assert "escape" not in _names(user.get("/api/browse", params={"path": "Topic"}))


# --- Security: a symlink whose realpath resolves INTO infra under BOOKS_DIR ----
#
# _safe_under_books re-applies _TOPDIR_SKIPLIST to the realpath-resolved target
# (flat .data/<hash> vault files exempted), so a benign-looking symlink path that
# dives into .data subdirs or the repo is 403'd for everyone — even though its
# realpath is still under BOOKS_DIR. The listing hides such entries too, so the
# read gate and the listing agree.

def test_symlink_into_vault_db_blocked(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    books_dir = _setup_tree(tmp_path, monkeypatch, helpers)
    # A real infra file inside the vault, surfaced via a benign-looking symlink.
    dbdir = os.path.join(books_dir, ".data", "db")
    os.makedirs(dbdir, exist_ok=True)
    with open(os.path.join(dbdir, "lib.db"), "wb") as f:
        f.write(b"SQLite format 3\x00secret")
    link = os.path.join(books_dir, "Topic", "leak.db")
    os.symlink(os.path.relpath(os.path.join(dbdir, "lib.db"), os.path.dirname(link)), link)

    helpers["make_user"]("reader@example.com")
    helpers["make_user"]("admin@example.com", admin=True)
    user = helpers["client_for"]("reader@example.com")
    admin = helpers["client_for"]("admin@example.com")
    from fastapi.testclient import TestClient
    guest = TestClient(main.app)

    # leak.db resolves into .data/db/ (not a flat vault file) → 403 for everyone.
    for client in (guest, user, admin):
        assert client.get("/api/files/Topic/leak.db").status_code == 403
    # ...and it is not surfaced in the (otherwise visible) Topic listing.
    assert "leak.db" not in _names(user.get("/api/browse", params={"path": "Topic"}))
    assert "leak.db" not in _names(admin.get("/api/browse", params={"path": "Topic"}))


def test_symlink_into_repo_source_blocked(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    books_dir = _setup_tree(tmp_path, monkeypatch, helpers)
    repo = os.path.join(books_dir, "urantia-library", "webapp", "backend")
    os.makedirs(repo, exist_ok=True)
    with open(os.path.join(repo, "security.py"), "w") as f:
        f.write("JWT_SECRET_KEY = 'do-not-leak'\n")
    # A .md-named symlink whose realpath is repo source (sanitize_text_path would
    # accept the .md extension; _safe_under_books must reject the resolved path).
    link = os.path.join(books_dir, "Topic", "peek.md")
    os.symlink(os.path.relpath(os.path.join(repo, "security.py"), os.path.dirname(link)), link)

    helpers["make_user"]("reader@example.com")
    user = helpers["client_for"]("reader@example.com")
    assert user.get("/api/files/Topic/peek.md").status_code == 403
    assert user.get("/api/md-content", params={"path": "Topic/peek.md"}).status_code == 403
    assert "peek.md" not in _names(user.get("/api/browse", params={"path": "Topic"}))


def test_orphan_vault_symlink_still_readable_after_infra_guard(app_ctx, tmp_path, monkeypatch):
    """The infra-target guard must NOT block a flat .data/<hash> vault file — that
    is what every legitimate (and orphan) book symlink resolves to."""
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    books_dir = _setup_tree(tmp_path, monkeypatch, helpers)
    orphan = ("d0" + "44" * 31)[:64]
    vault = os.path.join(books_dir, ".data", orphan)
    with open(vault, "wb") as f:
        f.write(b"orphan-bytes")
    link = os.path.join(books_dir, "Topic", "orphan2.txt")
    os.symlink(os.path.relpath(vault, os.path.dirname(link)), link)

    helpers["make_user"]("reader@example.com")
    user = helpers["client_for"]("reader@example.com")
    r = user.get("/api/files/Topic/orphan2.txt")
    assert r.status_code == 200, r.text
    assert r.content == b"orphan-bytes"
    assert "orphan2.txt" in _names(user.get("/api/browse", params={"path": "Topic"}))


# --- No-leak invariant: a gated topic with a stray foreign file stays hidden ---
#
# The reveal is "unmanaged-only": a directory that holds ANY registered book
# (readable or clearance-gated) is a managed topic governed by clearance, so a
# stray non-book file dropped in it must NOT reveal the topic's name (or let its
# bytes be read) to a user who can't read its books.

def test_gated_topic_with_stray_file_stays_hidden(app_ctx, tmp_path, monkeypatch):
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]
    books_dir = _setup_tree(tmp_path, monkeypatch, helpers)
    # Secret/ already has a clearance-100 registered book; drop a stray foreign file.
    with open(os.path.join(books_dir, "Secret", "stray.txt"), "wb") as f:
        f.write(b"stray")
    helpers["make_user"]("reader@example.com")  # clearance 0
    c = helpers["client_for"]("reader@example.com")

    # Discovery is blocked: the stray file must not flip the managed gated topic
    # into a revealed one. Secret stays out of the root listing and 403s on direct
    # browse navigation — its name (the topic's existence) does not leak.
    assert "Secret" not in _names(c.get("/api/browse", params={"path": ""}))
    assert c.get("/api/browse", params={"path": "Secret"}).status_code == 403
    # NOTE: the stray file is itself a foreign file, so a signed-in user who
    # ALREADY knows its exact path can still read it directly — the unmanaged-only
    # policy gates discovery (listing/navigation), not direct-path reads of a
    # foreign file. Pinned here so the residual is explicit, not accidental.
    assert c.get("/api/files/Secret/stray.txt").status_code == 200
