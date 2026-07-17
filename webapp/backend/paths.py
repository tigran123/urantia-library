"""Foundation module (extracted from main.py): path-safety and access-control
helpers — `_safe_under_books`, `_resolves_into_infra`, the per-format
`sanitize_*_path` guards, and the clearance/location query helpers. Depends on
config, cas, deps, models, database."""
import os
from fastapi import HTTPException
from sqlalchemy.orm import Session
import models
from cas import _resolve_vault_hash
from deps import _clearance_of, _is_admin
from config import _TOPDIR_SKIPLIST, _escape_like, CODE_EXTENSIONS


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect the
    library root via `monkeypatch.setattr(main, "BOOKS_DIR", ...)` / DATA_DIR, so
    these mutable runtime paths are read through `main` (the patch target) rather
    than bound at import. main re-exports them from config, so production reads the
    same config values. Call-time import: no load-time paths<->main cycle."""
    import main
    return main


def _safe_path_segment(seg: str) -> str:
    """Validate a single path segment (no separators, no traversal, no
    leading dot, non-empty). Returns the normalised segment."""
    seg = (seg or "").strip().strip("/")
    if not seg:
        raise HTTPException(status_code=400, detail="Empty path segment")
    if "/" in seg or "\\" in seg or seg in (".", "..") or seg.startswith("."):
        raise HTTPException(status_code=400, detail=f"Invalid path segment: {seg!r}")
    return seg


def _safe_subpath(sub: str) -> str:
    """Validate an optional multi-segment subpath. Empty string is OK."""
    sub = (sub or "").strip().strip("/")
    if not sub:
        return ""
    parts = [p for p in sub.split("/") if p]
    return "/".join(_safe_path_segment(p) for p in parts)


def _resolves_into_infra(abs_path: str) -> bool:
    """True if `abs_path`'s realpath lands on a `_TOPDIR_SKIPLIST` first component
    under BOOKS_DIR — a symlink with a benign lexical path that resolves *into*
    infra (`.data/db/lib.db`, `.data/covers/<hash>.jpg`, the repo under
    `urantia-library/`, …). The lone exemption is a flat `.data/<hash>` regular
    file: exactly what a legitimate book symlink resolves to (mirrors
    `_resolve_vault_hash`'s flat-only acceptance). Out-of-tree escapes return
    False here — the realpath *containment* check owns those. Shared by
    `_safe_under_books` (blocks the read) and `_list_directory` (hides the entry)
    so the listing and the content endpoints agree on what's reachable."""
    real_base = os.path.realpath(_m().BOOKS_DIR)
    real_target = os.path.realpath(abs_path)
    if real_target != real_base and not real_target.startswith(real_base + os.sep):
        return False
    real_data = os.path.realpath(_m().DATA_DIR)
    if os.path.dirname(real_target) == real_data and os.path.isfile(real_target):
        return False  # flat .data/<hash> vault file — a legitimate book
    real_rel = os.path.relpath(real_target, real_base)
    return real_rel != "." and real_rel.split(os.sep, 1)[0] in _TOPDIR_SKIPLIST

def _first_book_path(db: Session, hash_id: str) -> str | None:
    """Return one current symlink path for `hash_id`, or None if the book has
    no registered locations. Used by audit-write sites to snapshot a /item/<path>
    link target — the path freezes at audit time, so a later move/delete will
    surface to the admin as a broken link (intentional)."""
    row = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == hash_id
    ).first()
    return row[0] if row else None

def _primary_topic_path(db: Session, hash_id: str) -> str | None:
    """Return a current symlink path for `hash_id` that is NOT under
    Recommended/, or None. Used when recording usage events for
    recommend/unrecommend so the timeline's Path column shows the book's real
    topic location (e.g. Religions/Urantia/...) instead of the API URL or the
    synthetic Recommended/ symlink."""
    row = (
        db.query(models.BookLocation.symlink_path)
        .filter(
            models.BookLocation.hash_id == hash_id,
            models.BookLocation.symlink_path.notlike("Recommended/%"),
        )
        .first()
    )
    return row[0] if row else None

def _book_clearance(file_hash: str | None, db: Session) -> int:
    """Return the clearance required to read `file_hash`. 0 (public) if the
    hash is unknown or has no row in `books` — matches the design decision
    that ancillary, unregistered files are unrestricted."""
    if not file_hash:
        return 0
    book = db.query(models.Book).filter(models.Book.id == file_hash).first()
    return book.clearance if book else 0

def assert_can_read_path(symlink_fs_path: str, user: models.User | None, db: Session) -> None:
    """Resolve `symlink_fs_path` through the CAS vault, look up the book's
    clearance, and 403 if the user's clearance is below it. Admins bypass.
    A `None` user is an anonymous guest (clearance 0).

    Foreign / unregistered files (anything with no `books` row — a plain file, a
    non-vault symlink, or an orphan vault symlink) are readable by ANY signed-in
    user regardless of clearance, but NEVER by guests. We can't fold this into
    `_book_clearance` (which returns 0 for both "no book row" and "clearance-0
    book") because a registered clearance-0 ("public") book must stay
    guest-readable while a foreign file must not."""
    if _is_admin(user):
        return
    file_hash = _resolve_vault_hash(symlink_fs_path)
    book = db.query(models.Book).filter(models.Book.id == file_hash).first() if file_hash else None
    if book is None:
        if user is None:
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    if (book.clearance or 0) > _clearance_of(user):
        raise HTTPException(status_code=403, detail="Forbidden")

def _accessible_locations_query(db: Session, prefix: str, user: models.User | None):
    """Query for `book_locations.symlink_path` values under `prefix` that point
    to books readable by `user`. `prefix` should already end with '/' (or be ''
    for the library root). Relies on the PK index on symlink_path. LIKE
    metacharacters in `prefix` (a "_" or "%" in a directory name) are escaped so
    the prefix can't wildcard-match into sibling directories."""
    return (
        db.query(models.BookLocation.symlink_path)
        .join(models.Book, models.Book.id == models.BookLocation.hash_id)
        .filter(models.Book.clearance <= _clearance_of(user))
        .filter(models.BookLocation.symlink_path.like(f"{_escape_like(prefix)}%", escape="\\"))
    )

def _subtree_is_unmanaged(dir_fs_path: str, db: Session) -> bool:
    """True if `dir_fs_path` is an UN-IMPORTED area: its subtree holds at least
    one file but NO registered book (no `book_locations` row anywhere under it) —
    e.g. /Books/Unsorted, where files sit awaiting import. Such a directory
    contains only foreign files (readable by any signed-in user regardless of
    clearance), so it is revealed to logged-in non-admins even though it has no
    readable registered book.

    A directory that DOES contain registered books — readable OR clearance-gated —
    is a managed topic whose visibility is governed entirely by the clearance
    filter, so this returns False and the topic stays hidden. That keeps a gated
    topic from leaking its name (or a stray, un-imported file's bytes) just
    because a non-book file was dropped in it, and it bounds the cost: when any
    registered book exists we skip the filesystem walk entirely, and an unmanaged
    tree returns on its first file. Guests never reach here (the callers
    short-circuit on `current_user is None`)."""
    # Never reveal (or walk) a directory symlink that escapes the tree: its
    # contents 403 via _safe_under_books anyway, but we must not even surface its
    # name. Legit book symlinks resolve into .data (under BOOKS_DIR), so real
    # in-tree directories are unaffected.
    real_base = os.path.realpath(_m().BOOKS_DIR)
    real_dir = os.path.realpath(dir_fs_path)
    if real_dir != real_base and not real_dir.startswith(real_base + os.sep):
        return False
    rel_dir = os.path.relpath(dir_fs_path, _m().BOOKS_DIR).replace("\\", "/")
    prefix = "" if rel_dir in ("", ".") else f"{rel_dir.rstrip('/')}/"
    # Any registered book anywhere under here → managed topic → not unmanaged;
    # bail before touching the filesystem. Escape LIKE metacharacters so a
    # directory named e.g. "Vol_1" doesn't let the "_" wildcard match siblings
    # (consistent with the other escaped prefix scans in this file).
    has_registered = (
        db.query(models.BookLocation.symlink_path)
        .filter(models.BookLocation.symlink_path.like(f"{_escape_like(prefix)}%", escape="\\"))
        .first() is not None
    )
    if has_registered:
        return False
    for root, dirs, files in os.walk(dir_fs_path):
        dirs[:] = [d for d in dirs if d not in _TOPDIR_SKIPLIST]
        if files:
            return True
    return False

def _safe_under_books(path: str) -> str:
    """Resolve a user-supplied relative path under BOOKS_DIR with the standard
    traversal guard, and reject the infra entries in _TOPDIR_SKIPLIST. Returns the
    absolute path (existence is the caller's concern)."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    base = os.path.abspath(_m().BOOKS_DIR)
    target = os.path.abspath(os.path.join(_m().BOOKS_DIR, path))
    if target != base and not target.startswith(base + os.sep):
        raise HTTPException(status_code=403, detail="Forbidden")
    # abspath() is purely lexical, so a symlinked *directory component* (e.g.
    # Unsorted/link-out → /etc) would slip past the prefix check and let the
    # importer read/copy files from outside the tree. realpath() resolves the
    # symlinks; the library's own book symlinks still resolve within BOOKS_DIR
    # (into .data), so legitimate paths are unaffected.
    real_base = os.path.realpath(_m().BOOKS_DIR)
    real_target = os.path.realpath(target)
    if real_target != real_base and not real_target.startswith(real_base + os.sep):
        raise HTTPException(status_code=403, detail="Forbidden")
    # The lexical skiplist check below only inspects the *user-supplied* first
    # component, so a symlink with a benign path (e.g. Topic/leak) whose realpath
    # dives into infra that lives UNDER BOOKS_DIR — .data/db/lib.db,
    # .data/covers/<hash>.jpg, urantia-library/webapp/secrets.env — would sail
    # past it (the realpath guard above only blocks escaping *out* of the tree).
    # Re-apply the skiplist to the *resolved* target (flat .data/<hash> vault
    # files exempted, so books keep working).
    if _resolves_into_infra(target):
        raise HTTPException(status_code=403, detail="Forbidden")
    rel = os.path.relpath(target, base)
    if rel != "." and rel.split(os.sep, 1)[0] in _TOPDIR_SKIPLIST:
        raise HTTPException(status_code=403, detail="Forbidden")
    return target

def _assert_mutation_path(abs_path: str, *, is_dir: bool = False) -> None:
    """Realpath guard for admin filesystem-mutation targets (symlink create/
    remove, makedirs, rmtree). Complements the callers' lexical abspath checks:
    abspath() never resolves symlinks, so a symlinked directory *component*
    with a benign name (Topic/escape → /tmp/outside) slips the prefix check and
    would redirect the mutation outside the tree — or into infra that lives
    under BOOKS_DIR. The target need not exist: realpath() resolves the
    existing prefix, which is exactly where a planted symlink would sit.

    is_dir=False resolves only the parent chain, because the operation acts on
    the leaf itself (creating or removing a symlink) — a book symlink's leaf
    legitimately resolves into .data, and a dangling one (vault file already
    gone) must stay deletable, which a full resolve would refuse via the
    skiplist. is_dir=True (makedirs/rmtree targets) resolves the leaf too,
    since those operations follow a symlinked leaf."""
    checked = abs_path.rstrip(os.sep)
    if not is_dir:
        checked = os.path.dirname(checked)
    real_base = os.path.realpath(_m().BOOKS_DIR)
    real_checked = os.path.realpath(checked)
    if real_checked != real_base and not real_checked.startswith(real_base + os.sep):
        raise HTTPException(status_code=403, detail="Forbidden")
    rel = os.path.relpath(real_checked, real_base)
    if rel != "." and rel.split(os.sep, 1)[0] in _TOPDIR_SKIPLIST:
        raise HTTPException(status_code=403, detail="Forbidden")

def sanitize_fb2_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR (realpath + skiplist guard via
    _safe_under_books), exists, and is an FB2 (or .fb2.zip) file."""
    target_path = _safe_under_books(path)
    if not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    if not (lower.endswith(".fb2") or lower.endswith(".fb2.zip")):
        raise HTTPException(status_code=400, detail="Not an FB2 file")
    return target_path

def sanitize_text_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR (realpath + skiplist guard via
    _safe_under_books), exists, and is a .md/.markdown/.txt (optionally
    .zip-wrapped) or code file. The skiplist rejection matters most here: this
    viewer renders code/.md, so without it a signed-in user could read repo
    source under urantia-library/ (e.g. security.py) once the file is reachable."""
    target_path = _safe_under_books(path)
    if not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    ext = os.path.splitext(lower)[1]
    if not (lower.endswith((".md", ".markdown", ".txt", ".txt.zip", ".md.zip", ".markdown.zip"))
            or ext in CODE_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Not a Markdown, text, or supported code file")
    return target_path

def sanitize_html_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR (realpath + skiplist guard via
    _safe_under_books), exists, and is an .html/.htm/.html.zip file."""
    target_path = _safe_under_books(path)
    if not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    if not (lower.endswith(".html") or lower.endswith(".htm")
            or lower.endswith(".html.zip") or lower.endswith(".htm.zip")):
        raise HTTPException(status_code=400, detail="Not an HTML file")
    return target_path

def sanitize_djvu_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR (realpath + skiplist guard via
    _safe_under_books), exists, and is a .djvu file."""
    target_path = _safe_under_books(path)
    if not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    if not target_path.lower().endswith(".djvu"):
        raise HTTPException(status_code=400, detail="Not a DjVu file")
    return target_path
