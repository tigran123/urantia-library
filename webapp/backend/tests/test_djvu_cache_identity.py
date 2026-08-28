"""libdjvu caches decoded documents by URI, so DjVu paths must be canonicalised
to the content-addressed vault file before they reach it.

A book's symlink path is stable across an admin file replacement. Handing that
path to libdjvu means a worker that already decoded the previous bytes keeps
serving them — a stale table of contents and page count, and a 500 once the
superseded vault file is removed out from under the cached document. Resolving
to .data/<blake2b> makes the URI change exactly when the content does.

This asserts the property directly (what path reaches libdjvu) rather than
needing a real multi-page DjVu fixture and the djvulibre CLI tools.
"""
from __future__ import annotations

import os

import pytest

from .test_upload_commit import upload_ctx  # noqa: F401 — fixture


DJVU_STUB = b"AT&TFORM" + b"\x00" * 64          # passes _detect_format/_staged_reads_as


@pytest.fixture
def djvu_book(upload_ctx):
    """A symlink under BOOKS_DIR pointing into the vault, as a real book is."""
    _helpers, _client, books, data, _main = upload_ctx
    vault = data / ("f" * 128)
    vault.write_bytes(DJVU_STUB)
    link = books / "Topic" / "book.djvu"
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.relpath(vault, link.parent), link)
    return str(link), str(vault)


def _captured_uri(monkeypatch, fn, path):
    """Run `fn(path)` with libdjvu stubbed out, returning the path it was given."""
    import djvu.decode
    seen = {}

    def fake_file_uri(p):
        seen["path"] = p
        raise RuntimeError("stop before decoding")

    monkeypatch.setattr(djvu.decode, "FileURI", fake_file_uri)
    with pytest.raises(RuntimeError):
        fn(path)
    return seen.get("path")


def test_outline_decodes_the_vault_file_not_the_symlink(djvu_book, monkeypatch):
    link, vault = djvu_book
    from routers.content import extract_djvu_outline

    got = _captured_uri(monkeypatch, extract_djvu_outline, link)
    assert got == os.path.realpath(vault), (
        "libdjvu must receive the content-addressed vault path; handing it the "
        "symlink path lets a cached pre-replace document be served forever"
    )
    assert got != link


def test_uri_follows_the_symlink_across_a_replace(djvu_book):
    """The whole point: re-pointing the symlink changes the URI libdjvu sees."""
    link, vault = djvu_book
    from routers.content import _djvu_uri

    before = _djvu_uri(link)

    # Swap in new bytes under a new hash, exactly as the replace endpoint does.
    new_vault = os.path.join(os.path.dirname(vault), "e" * 128)
    with open(new_vault, "wb") as f:
        f.write(DJVU_STUB + b"corrected")
    tmp = link + ".tmp"
    os.symlink(os.path.relpath(new_vault, os.path.dirname(link)), tmp)
    os.replace(tmp, link)
    os.remove(vault)

    after = _djvu_uri(link)
    assert after != before, "URI must change with the content, or libdjvu serves stale bytes"
    assert after == os.path.realpath(new_vault)
