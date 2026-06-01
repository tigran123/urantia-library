"""End-to-end coverage for the admin upload → preview → commit flow.

The flow writes to the CAS vault, creates symlinks under BOOKS_DIR and parks
staged files under STAGING_DIR. conftest only redirects the DB, so the
`upload_ctx` fixture additionally repoints those four module globals (read at
call time in main.py) at a throwaway tmp tree.

Covers the new behaviour added alongside these tests:
- content-aware extension validation (commit + relaxed staging preview gates), and
- .txt.zip / .md.zip / .markdown.zip unzip-on-read.
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile

import pytest


# ---- sample fixtures (pure-python; no external binaries needed) -------------

FB2_XML = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
    b'xmlns:l="http://www.w3.org/1999/xlink">'
    b'<description><title-info><book-title>Test Book</book-title></title-info></description>'
    b'<body><section><p>Hello world</p></section></body>'
    b'</FictionBook>'
)
HTML_DOC = b"<!doctype html><html><head><title>H</title></head><body><h1>Hi</h1></body></html>"
PDF_STUB = b"%PDF-1.4\n%fake pdf for tests\n"
DOCX_STUB = b"PK\x03\x04 not really a docx, just needs to upload"


def _zip_bytes(inner_name: str, inner_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, inner_bytes)
    return buf.getvalue()


def _write(abs_path: str, data: bytes = b"x") -> None:
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(data)


# ---- harness ----------------------------------------------------------------

@pytest.fixture
def upload_ctx(app_ctx, tmp_path, monkeypatch):
    """(helpers, client, books_dir, data_dir, main) with the library filesystem
    redirected to tmp and an authenticated admin client ready."""
    helpers, _captured, _TestSession = app_ctx
    main = helpers["main"]

    books = tmp_path / "books"
    data = books / ".data"
    staging = data / "staging"
    covers = data / "covers"
    for d in (books, data, staging, covers):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "BOOKS_DIR", str(books))
    monkeypatch.setattr(main, "DATA_DIR", str(data))
    monkeypatch.setattr(main, "STAGING_DIR", str(staging))
    monkeypatch.setattr(main, "COVERS_DIR", str(covers))

    helpers["make_user"]("admin@example.com", admin=True)
    client = helpers["client_for"]("admin@example.com")
    return helpers, client, books, data, main


def upload(client, filename: str, raw: bytes):
    return client.post(
        "/api/admin/books/upload",
        files={"file": (filename, raw, "application/octet-stream")},
    )


def parse_done(resp) -> dict:
    """Pull the `done` SSE event payload out of the streamed upload response."""
    assert resp.status_code == 200, resp.text
    for block in resp.text.split("\n\n"):
        if not block.strip():
            continue
        ev = data = None
        for ln in block.split("\n"):
            if ln.startswith("event:"):
                ev = ln[len("event:"):].strip()
            elif ln.startswith("data:"):
                data = ln[len("data:"):].strip()
        if ev == "done":
            return json.loads(data)
    raise AssertionError(f"no done event in stream: {resp.text!r}")


def commit(client, staging_id, *, filename=None, top_dir="Test", subpath="",
           clearance=0, metadata=None, batch_id=None):
    body = {
        "staging_id": staging_id,
        "metadata": metadata or {"title": "T"},
        "top_dir": top_dir,
        "subpath": subpath,
        "clearance": clearance,
        "needs_review": False,
    }
    if filename is not None:
        body["filename"] = filename
    if batch_id is not None:
        body["batch_id"] = batch_id
    return client.post("/api/admin/books/commit", json=body)


# ---- end-to-end tests -------------------------------------------------------

def test_fb2_upload_and_commit(upload_ctx):
    helpers, client, books, data, main = upload_ctx
    done = parse_done(upload(client, "book.fb2", FB2_XML))
    assert "error" not in done, done
    # .fb2 is re-zipped to .fb2.zip on upload.
    assert done["format"] == "fb2.zip"
    assert done["filename"] == "book.fb2.zip"

    res = commit(client, done["staging_id"])
    assert res.status_code == 200, res.text
    detail = res.json()
    assert detail["id"] == done["hash"]
    assert os.path.exists(data / detail["id"])                 # vault file
    link = books / "Test" / "book.fb2.zip"
    assert os.path.islink(link) and os.path.exists(link)       # resolvable symlink

    models = helpers["models"]
    db = helpers["TestSession"]()
    try:
        assert db.query(models.Book).filter_by(id=detail["id"]).first() is not None
        assert db.query(models.BookLocation).filter_by(
            symlink_path="Test/book.fb2.zip").first() is not None
    finally:
        db.close()


def test_batch_upload_folds_into_one_audit_row(upload_ctx):
    helpers, client, _b, _d, _m = upload_ctx
    fb2b = FB2_XML.replace(b"Test Book", b"Volume Two")   # distinct bytes → distinct hash
    sid1 = parse_done(upload(client, "tom01.fb2", FB2_XML))["staging_id"]
    sid2 = parse_done(upload(client, "tom02.fb2", fb2b))["staging_id"]
    assert commit(client, sid1, top_dir="Set", metadata={"title": "Vol 1"}, batch_id="bX").status_code == 200
    assert commit(client, sid2, top_dir="Set", metadata={"title": "Vol 2"}, batch_id="bX").status_code == 200

    models = helpers["models"]
    db = helpers["TestSession"]()
    try:
        rows = db.query(models.AdminAuditLog).filter_by(action="book.upload").all()
        assert len(rows) == 1                       # folded into ONE row, not two
        row = rows[0]
        assert row.target_kind == "book_batch"
        assert row.target_id == "bX"
        details = json.loads(row.details_json)
        assert details["count"] == 2
        assert [b["title"] for b in details["books"]] == ["Vol 1", "Vol 2"]
        assert details["dir"] == "Set"
    finally:
        db.close()


def test_single_commit_keeps_per_book_audit(upload_ctx):
    helpers, client, _b, _d, _m = upload_ctx
    sid = parse_done(upload(client, "solo.fb2", FB2_XML))["staging_id"]
    assert commit(client, sid, metadata={"title": "Solo"}).status_code == 200
    models = helpers["models"]
    db = helpers["TestSession"]()
    try:
        row = db.query(models.AdminAuditLog).filter_by(action="book.upload").one()
        assert row.target_kind == "book"            # individual, not batched
        assert json.loads(row.details_json)["title"] == "Solo"
    finally:
        db.close()


def test_fb2_preview(upload_ctx):
    _h, client, _b, _d, _m = upload_ctx
    done = parse_done(upload(client, "book.fb2", FB2_XML))
    res = client.get(f"/api/admin/books/upload/{done['staging_id']}/fb2-content")
    assert res.status_code == 200, res.text
    assert res.json()["html"]


def test_fb2_preview_rejects_non_fb2(upload_ctx):
    # Relaxed gate: a non-FB2 staged file fails in the reader with a clean 422.
    _h, client, _b, _d, _m = upload_ctx
    done = parse_done(upload(client, "plain.txt", b"just some text, not fb2"))
    res = client.get(f"/api/admin/books/upload/{done['staging_id']}/fb2-content")
    assert res.status_code == 422, res.text


def test_content_aware_commit_valid_relabel(upload_ctx):
    # A plain .txt genuinely reads as markdown — relabel .txt -> .md is allowed.
    _h, client, books, _d, _m = upload_ctx
    done = parse_done(upload(client, "notes.txt", b"# Heading\n\nbody text"))
    res = commit(client, done["staging_id"], filename="notes.md")
    assert res.status_code == 200, res.text
    assert os.path.exists(books / "Test" / "notes.md")


def test_content_aware_commit_invalid_relabel(upload_ctx):
    # A PDF is not a DjVu — relabel .pdf -> .djvu is rejected by the probe.
    _h, client, _b, _d, _m = upload_ctx
    done = parse_done(upload(client, "doc.pdf", PDF_STUB))
    res = commit(client, done["staging_id"], filename="doc.djvu")
    assert res.status_code == 400
    assert "valid DJVU" in res.json()["detail"]


def test_lexical_fallback_no_probe(upload_ctx):
    # No content probe for .odt -> keep the lexical "must keep extension" lock.
    _h, client, _b, _d, _m = upload_ctx
    done = parse_done(upload(client, "file.docx", DOCX_STUB))
    res = commit(client, done["staging_id"], filename="file.odt")
    assert res.status_code == 400
    assert "must keep extension" in res.json()["detail"]


def test_djvu_preview_rejects_non_djvu(upload_ctx):
    # Relaxed gate: feeding a non-DjVu file to the djvu endpoint -> clean 422.
    _h, client, _b, _d, _m = upload_ctx
    done = parse_done(upload(client, "doc.pdf", PDF_STUB))
    res = client.get(f"/api/admin/books/upload/{done['staging_id']}/djvu-metadata")
    assert res.status_code == 422, res.text


def test_txt_zip_end_to_end(upload_ctx):
    _h, client, _b, _d, _m = upload_ctx
    raw = _zip_bytes("notes.txt", b"plain text inside a zip")
    done = parse_done(upload(client, "notes.txt.zip", raw))
    assert "error" not in done, done

    # Staging preview unzips on the fly and renders as plain text (no TOC).
    pre = client.get(f"/api/admin/books/upload/{done['staging_id']}/md-content")
    assert pre.status_code == 200, pre.text
    assert "plain text inside a zip" in pre.json()["raw"]
    assert pre.json()["toc"] == []

    assert commit(client, done["staging_id"]).status_code == 200
    live = client.get("/api/md-content", params={"path": "Test/notes.txt.zip"})
    assert live.status_code == 200, live.text
    assert "plain text inside a zip" in live.json()["raw"]


def test_md_zip_end_to_end(upload_ctx):
    _h, client, _b, _d, _m = upload_ctx
    raw = _zip_bytes("doc.md", b"# Title\n\nSome **markdown** body.")
    done = parse_done(upload(client, "doc.md.zip", raw))
    assert "error" not in done, done

    assert commit(client, done["staging_id"]).status_code == 200
    live = client.get("/api/md-content", params={"path": "Test/doc.md.zip"})
    assert live.status_code == 200, live.text
    body = live.json()
    assert body["html"]            # rendered markdown
    assert body["toc"]             # heading collected into the TOC


def test_cancel_staging(upload_ctx):
    _h, client, _b, _d, main = upload_ctx
    done = parse_done(upload(client, "book.fb2", FB2_XML))
    sid = done["staging_id"]
    assert sid in main._STAGING
    res = client.delete(f"/api/admin/books/upload/{sid}")
    assert res.status_code == 200
    assert res.json() == {"cancelled": sid}
    assert sid not in main._STAGING


def test_touch_refreshes_only_requested_ids(upload_ctx):
    # Keepalive must refresh only the ids the caller still has open — not every
    # staging record the admin owns — so one tab can't keep abandoned uploads alive.
    _h, client, _b, _d, main = upload_ctx
    sid1 = parse_done(upload(client, "tom01.fb2", FB2_XML))["staging_id"]
    sid2 = parse_done(upload(client, "notes.txt", b"plain text"))["staging_id"]
    main._STAGING[sid1]["expires_at"] = 1.0
    main._STAGING[sid2]["expires_at"] = 1.0

    res = client.post("/api/admin/books/upload/touch", json={"staging_ids": [sid1]})
    assert res.status_code == 200
    assert res.json()["touched"] == 1
    assert main._STAGING[sid1]["expires_at"] > time.time() + 1000   # requested → refreshed
    assert main._STAGING[sid2]["expires_at"] == 1.0                  # not requested → left to expire


def test_purge_sweeps_orphan_dirs(upload_ctx):
    # A staging dir whose in-memory entry was lost (restart/crash) must still be
    # reaped by the disk-reconciling sweep, while fresh and tracked dirs survive.
    _h, _client, _b, _d, main = upload_ctx
    staging = main.STAGING_DIR
    old = time.time() - main._STAGING_TTL_S - 60

    orphan = os.path.join(staging, "orphan_old")
    os.makedirs(orphan, exist_ok=True)
    open(os.path.join(orphan, "f"), "w").close()
    os.utime(orphan, (old, old))

    fresh = os.path.join(staging, "fresh_untracked")
    os.makedirs(fresh, exist_ok=True)  # recent mtime → must be kept (in-flight guard)

    tracked = os.path.join(staging, "tracked_old")
    os.makedirs(tracked, exist_ok=True)
    os.utime(tracked, (old, old))
    main._STAGING["tracked_old"] = {"dir": tracked, "expires_at": time.time() + 9999, "owner_id": 0}

    try:
        main._purge_expired_staging()
        assert not os.path.exists(orphan)   # old + untracked → swept
        assert os.path.exists(fresh)        # recent → kept
        assert os.path.exists(tracked)      # tracked + not expired → kept despite age
    finally:
        main._STAGING.pop("tracked_old", None)


# ---- server-side import (stage-from-path / importable) ----------------------

def test_stage_from_path_copies_and_keeps_original(upload_ctx):
    _h, client, books, data, _m = upload_ctx
    src = os.path.join(str(books), "Unsorted", "book.fb2")
    _write(src, FB2_XML)

    res = client.post("/api/admin/books/stage-from-path", json={"path": "Unsorted/book.fb2"})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["format"] == "fb2.zip"           # rezipped in staging
    assert payload["filename"] == "book.fb2.zip"
    assert os.path.exists(src)                       # original untouched (copy semantics)

    c = commit(client, payload["staging_id"], top_dir="Imported", metadata={"title": "B"})
    assert c.status_code == 200, c.text
    assert os.path.exists(os.path.join(str(data), payload["hash"]))   # in the vault
    assert os.path.exists(src)                       # STILL in Unsorted after commit


def test_stage_from_path_duplicate(upload_ctx):
    _h, client, books, _d, _m = upload_ctx
    src = os.path.join(str(books), "Unsorted", "dup.fb2")
    _write(src, FB2_XML)
    p1 = client.post("/api/admin/books/stage-from-path", json={"path": "Unsorted/dup.fb2"}).json()
    assert commit(client, p1["staging_id"], top_dir="Imported", metadata={"title": "D"}).status_code == 200
    # original still present (copy) → re-staging it is detected as a duplicate
    res = client.post("/api/admin/books/stage-from-path", json={"path": "Unsorted/dup.fb2"})
    assert res.status_code == 200, res.text
    assert "existing" in res.json()


def test_importable_recursion_filters(upload_ctx):
    _h, client, books, _d, _m = upload_ctx
    base = os.path.join(str(books), "Unsorted", "Set")
    _write(os.path.join(base, "a.pdf"), PDF_STUB)
    _write(os.path.join(base, "b.djvu"), b"AT&TFORM stub")
    _write(os.path.join(base, "sub", "c.epub"), _zip_bytes("x.html", b"<html></html>"))
    _write(os.path.join(base, "note.bin"), b"not a book")
    os.symlink(os.path.join(str(books), "Unsorted", "missing-target"),
               os.path.join(base, "link.pdf"))   # symlink → excluded

    res = client.post("/api/admin/books/importable", json={"paths": ["Unsorted/Set"]})
    assert res.status_code == 200, res.text
    assert res.json()["files"] == [
        "Unsorted/Set/a.pdf", "Unsorted/Set/b.djvu", "Unsorted/Set/sub/c.epub",
    ]


def test_stage_from_path_traversal_blocked(upload_ctx):
    _h, client, _b, _d, _m = upload_ctx
    assert client.post("/api/admin/books/stage-from-path", json={"path": "../etc/passwd"}).status_code == 403
    assert client.post("/api/admin/books/stage-from-path", json={"path": ".data/anything"}).status_code == 403


# ---- pure-helper unit tests -------------------------------------------------

def test_effective_suffix(app_ctx):
    main = app_ctx[0]["main"]
    assert main._effective_suffix("a.fb2.zip") == ".fb2.zip"
    assert main._effective_suffix("a.txt.zip") == ".txt.zip"
    assert main._effective_suffix("a.pdf") == ".pdf"
    assert main._effective_suffix("noext") == ""


def test_text_inner_ext(app_ctx):
    main = app_ctx[0]["main"]
    assert main._text_inner_ext("notes.txt.zip") == ".txt"
    assert main._text_inner_ext("README.md") == ".md"
    assert main._text_inner_ext("a.markdown.zip") == ".markdown"


def test_read_text_bytes_unzips(app_ctx, tmp_path):
    main = app_ctx[0]["main"]
    p = tmp_path / "n.txt.zip"
    p.write_bytes(_zip_bytes("n.txt", b"hello zip"))
    assert main._read_text_bytes(str(p)) == b"hello zip"
    plain = tmp_path / "n.txt"
    plain.write_bytes(b"hello plain")
    assert main._read_text_bytes(str(plain)) == b"hello plain"


def test_staged_reads_as(app_ctx, tmp_path):
    main = app_ctx[0]["main"]

    fb2 = tmp_path / "a.fb2"; fb2.write_bytes(FB2_XML)
    assert main._staged_reads_as(str(fb2), ".fb2") is True
    assert main._staged_reads_as(str(fb2), ".fb2.zip") is False

    fb2zip = tmp_path / "a.fb2.zip"; fb2zip.write_bytes(_zip_bytes("a.fb2", FB2_XML))
    assert main._staged_reads_as(str(fb2zip), ".fb2.zip") is True
    assert main._staged_reads_as(str(fb2zip), ".fb2") is False

    pdf = tmp_path / "a.pdf"; pdf.write_bytes(PDF_STUB)
    assert main._staged_reads_as(str(pdf), ".pdf") is True
    assert main._staged_reads_as(str(pdf), ".djvu") is False

    txt = tmp_path / "a.txt"; txt.write_bytes(b"plain")
    assert main._staged_reads_as(str(txt), ".txt") is True
    assert main._staged_reads_as(str(txt), ".md") is True
    assert main._staged_reads_as(str(txt), ".txt.zip") is False  # not a zip

    txtzip = tmp_path / "a.txt.zip"; txtzip.write_bytes(_zip_bytes("a.txt", b"plain"))
    assert main._staged_reads_as(str(txtzip), ".txt.zip") is True

    # No probe for .odt -> None (caller keeps the lexical lock).
    docx = tmp_path / "a.docx"; docx.write_bytes(DOCX_STUB)
    assert main._staged_reads_as(str(docx), ".odt") is None
