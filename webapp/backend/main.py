from fastapi import FastAPI, HTTPException, Request, Depends, Cookie, status, Response, UploadFile, File
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, not_, func, case, select
from fastapi.responses import JSONResponse, FileResponse, Response, StreamingResponse
import os
import re
import subprocess
import io
import hashlib
import zipfile
import base64
import shutil
import uuid
import asyncio
import threading
import xml.etree.ElementTree as ET
from html import escape as _html_escape
from PIL import Image
import djvu.decode
from typing import List, Dict, Any, Optional
from pathlib import Path
import json
import logging
from datetime import datetime, timezone, timedelta
from fastapi.middleware.cors import CORSMiddleware
import models
import schemas
from database import engine, get_db, SessionLocal
from security import get_password_hash, verify_password, create_access_token, SECRET_KEY, ALGORITHM
import email_utils
import jwt

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def custom_logging_middleware(request: Request, call_next):
    # Determine user email from cookie
    user_email = "Anonymous"
    access_token = request.cookies.get("access_token")
    if access_token:
        try:
            payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
            user_email = payload.get("sub", "Anonymous")
        except:
            pass

    # Client IP without port
    client_host = request.client.host if request.client else "unknown"

    response = await call_next(request)

    # Format current date/time
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Format and print the log
    url_with_query = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
    print(f"{now} {client_host} - {user_email} - \"{request.method} {url_with_query} HTTP/{request.scope.get('http_version', '1.1')}\" {response.status_code}")

    return response

@app.middleware("http")
async def strip_charset_for_websites(request: Request, call_next):
    response = await call_next(request)
    if "/Websites/" in request.url.path:
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type and "charset=utf-8" in content_type.lower():
            # Remove the forced utf-8 charset so the browser relies on the <meta> tag inside the file
            # By deleting it and recreating, we ensure Starlette's MutableHeaders handles it properly
            del response.headers["content-type"]
            response.headers["content-type"] = "text/html"
    return response

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")
DATA_DIR = os.path.join(BOOKS_DIR, ".data")
_TOPDIR_SKIPLIST = {".claude", ".antigravitycli", ".vscode", ".data", "CLAUDE.md", "GEMINI.md", "urantia-library", "avatars"}


def _resolve_vault_hash(symlink_path: str) -> str | None:
    """Return the BLAKE2b hash if `symlink_path` resolves into the CAS vault
    (i.e. /Books/.data/<hash>); None for unrelated symlinks like
    /Books/GEMINI.md -> CLAUDE.md."""
    try:
        target = os.path.realpath(symlink_path)
    except OSError:
        return None
    data_root = os.path.realpath(DATA_DIR)
    if not target.startswith(data_root + os.sep):
        return None
    name = os.path.basename(target)
    # Vault entries are flat under .data/ — reject anything nested (e.g. covers/).
    if os.path.dirname(target) != data_root:
        return None
    return name or None


# ---------- Integrity verification ----------

_VERIFY_CHUNK = 8 * 1024 * 1024  # match migrate_library.py


def _blake2b_of_file(path: str) -> str:
    # Vault files are immutable post-migration, so concurrent reads from
    # downloads + this hash are safe on Linux (POSIX reads don't conflict).
    h = hashlib.blake2b()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_VERIFY_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _verify_book_sync(hash_id: str, mode: str, db: Session) -> dict:
    """Run integrity checks for a single book and persist the result.

    Returns a JSON-serialisable dict (see plan §B). Caller is responsible for
    running this inside a worker thread (asyncio.to_thread) because the
    full-mode hash recompute is CPU/IO-bound.
    """
    if mode not in ("quick", "full"):
        mode = "quick"

    checks: list[dict] = []
    error: Optional[str] = None
    db_update_failed = False

    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    checks.append({"name": "db_row", "ok": book is not None,
                   "detail": None if book else "no books row for this hash_id"})
    if not book:
        return {
            "hash_id": hash_id, "mode": mode, "ok": False, "error": "book_missing",
            "checks": checks, "verified_at": _now_iso(),
            "title": None, "original_filename": None, "db_update_failed": False,
        }

    title = book.title
    original_filename = book.original_filename

    data_path = os.path.join(DATA_DIR, hash_id)
    data_exists = os.path.isfile(data_path)
    checks.append({"name": "data_file_exists", "ok": data_exists,
                   "detail": None if data_exists else data_path})
    if not data_exists:
        error = "data_missing"

    size = 0
    if data_exists:
        try:
            size = os.path.getsize(data_path)
        except OSError as e:
            size = 0
            checks.append({"name": "data_file_size", "ok": False, "detail": f"stat failed: {e}"})
            error = error or "data_missing"
        else:
            size_ok = size > 0
            checks.append({"name": "data_file_size", "ok": size_ok,
                           "detail": {"bytes": size}})
            if not size_ok:
                error = error or "empty_file"

    locs = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == hash_id
    ).all()
    loc_paths = [r[0] for r in locs]
    checks.append({"name": "locations_present", "ok": len(loc_paths) > 0,
                   "detail": {"count": len(loc_paths)}})
    if not loc_paths:
        error = error or "locations_missing"

    bad_symlinks: list[dict] = []
    for sp in loc_paths:
        full = os.path.join(BOOKS_DIR, sp)
        if not os.path.islink(full):
            bad_symlinks.append({"symlink_path": sp, "reason": "not a symlink or missing"})
            continue
        resolved = _resolve_vault_hash(full)
        if resolved != hash_id:
            bad_symlinks.append({"symlink_path": sp,
                                 "reason": f"resolves to {resolved!r}, expected {hash_id}"})
    checks.append({"name": "symlinks_resolve",
                   "ok": not bad_symlinks,
                   "detail": bad_symlinks if bad_symlinks else None})
    if bad_symlinks:
        error = error or "symlink_broken"

    if mode == "full" and data_exists and size > 0:
        try:
            computed = _blake2b_of_file(data_path)
        except OSError as e:
            checks.append({"name": "hash_match", "ok": False, "detail": f"read failed: {e}"})
            error = error or "data_missing"
        else:
            match = computed == hash_id
            checks.append({"name": "hash_match", "ok": match,
                           "detail": None if match else f"computed {computed}"})
            if not match:
                error = error or "hash_mismatch"

    ok = error is None
    verified_at = _now_iso()

    try:
        book.last_verified_at = verified_at
        book.last_verified_ok = ok
        book.last_verified_mode = mode
        book.last_verified_error = None if ok else error
        db.commit()
    except Exception as e:
        logging.warning("failed to persist last_verified_* for %s: %s", hash_id, e)
        db.rollback()
        db_update_failed = True

    return {
        "hash_id": hash_id, "mode": mode, "ok": ok, "error": error,
        "checks": checks, "verified_at": verified_at,
        "title": title, "original_filename": original_filename,
        "db_update_failed": db_update_failed,
    }

async def get_current_user(access_token: str = Cookie(None), db: Session = Depends(get_db)):
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_admin(current_user: models.User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return current_user


async def get_optional_user(
    access_token: str = Cookie(None), db: Session = Depends(get_db)
) -> models.User | None:
    """Like `get_current_user`, but returns `None` instead of raising 401 when
    there is no valid session. Used by guest-reachable endpoints: a `None` user
    is an anonymous visitor, treated as clearance 0 (see `_clearance_of` /
    `_is_admin`). Never raises."""
    if not access_token:
        return None
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
    except jwt.PyJWTError:
        return None
    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None or not user.is_active:
        return None
    return user


def _clearance_of(user: models.User | None) -> int:
    """Clearance for a possibly-anonymous user; guests (`None`) are clearance 0."""
    return (user.clearance or 0) if user else 0


def _is_admin(user: models.User | None) -> bool:
    """True only for a signed-in admin; guests (`None`) are never admin."""
    return bool(user and user.is_admin)


def _book_clearance(file_hash: str | None, db: Session) -> int:
    """Return the clearance required to read `file_hash`. 0 (public) if the
    hash is unknown or has no row in `books` — matches the design decision
    that ancillary, unregistered files are unrestricted."""
    if not file_hash:
        return 0
    book = db.query(models.Book).filter(models.Book.id == file_hash).first()
    return book.clearance if book else 0


def _rating_stats(db: Session, hash_ids) -> dict[str, dict]:
    """avg_rating + rating_count per hash_id, computed in ONE GROUP BY query.
    Hashes with no ratings are simply absent from the returned dict — callers
    default those to {avg_rating: None, rating_count: 0}."""
    ids = {h for h in hash_ids if h}
    if not ids:
        return {}
    rows = (
        db.query(
            models.BookRating.hash_id,
            func.avg(models.BookRating.rating),
            func.count(models.BookRating.id),
        )
        .filter(models.BookRating.hash_id.in_(ids))
        .group_by(models.BookRating.hash_id)
        .all()
    )
    return {
        hid: {"avg_rating": round(float(avg), 3), "rating_count": int(cnt)}
        for hid, avg, cnt in rows
    }


def assert_can_read_path(symlink_fs_path: str, user: models.User | None, db: Session) -> None:
    """Resolve `symlink_fs_path` through the CAS vault, look up the book's
    clearance, and 403 if the user's clearance is below it. Admins bypass.
    Non-CAS files (symlinks pointing outside .data) are treated as public.
    A `None` user is an anonymous guest (clearance 0)."""
    if _is_admin(user):
        return
    file_hash = _resolve_vault_hash(symlink_fs_path)
    required = _book_clearance(file_hash, db)
    if required > _clearance_of(user):
        raise HTTPException(status_code=403, detail="Forbidden")


def _accessible_locations_query(db: Session, prefix: str, user: models.User | None):
    """Query for `book_locations.symlink_path` values under `prefix` that point
    to books readable by `user`. `prefix` should already end with '/' (or be ''
    for the library root). Relies on the PK index on symlink_path."""
    return (
        db.query(models.BookLocation.symlink_path)
        .join(models.Book, models.Book.id == models.BookLocation.hash_id)
        .filter(models.Book.clearance <= _clearance_of(user))
        .filter(models.BookLocation.symlink_path.like(f"{prefix}%"))
    )


@app.post("/api/login")
async def login(login_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == login_data.email).first()
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="User is not active")

    access_token = create_access_token(data={"sub": user.email})
    response = JSONResponse(content={"message": "Login successful"})
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "true").lower() != "false",
        samesite="lax",
        max_age=7*24*60*60
    )
    return response

@app.post("/api/logout")
async def logout():
    response = JSONResponse(content={"message": "Logout successful"})
    response.delete_cookie(key="access_token")
    return response

@app.get("/api/me", response_model=schemas.UserResponse)
async def get_me(current_user: models.User = Depends(get_current_user)):
    return {
        "email": current_user.email,
        "avatar_url": current_user.avatar_url,
        "real_name": current_user.real_name,
        "search_per_page": current_user.search_per_page,
        "is_admin": bool(current_user.is_admin),
        "clearance": int(current_user.clearance or 0),
    }

@app.put("/api/users/me/settings", response_model=schemas.UserResponse)
async def update_settings(
    settings: schemas.UserSettingsUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if settings.search_per_page is not None:
        current_user.search_per_page = max(10, min(settings.search_per_page, 200))
    if settings.real_name is not None:
        # Empty (or whitespace-only) clears the name; otherwise trim and cap length.
        cleaned = settings.real_name.strip()[:100]
        current_user.real_name = cleaned or None
    db.commit()
    db.refresh(current_user)
    return current_user

os.makedirs("avatars", exist_ok=True)
app.mount("/api/avatars", StaticFiles(directory="avatars"), name="avatars")

@app.post("/api/users/me/avatar", response_model=schemas.UserResponse)
async def upload_avatar(file: UploadFile = File(...), current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Invalid image file")

    ext = file.filename.split(".")[-1]
    filename = f"{current_user.id}_{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join("avatars", filename)

    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    avatar_url = f"/api/avatars/{filename}"
    current_user.avatar_url = avatar_url
    db.commit()
    db.refresh(current_user)

    return current_user

@app.get("/api/browse")
async def browse(path: str = "", current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    target_dir = os.path.join(BOOKS_DIR, path)
    if not os.path.abspath(target_dir).startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    # For non-admins, hide subdirectories whose subtree contains no readable book
    # (and 403 on direct access to such a directory) so the topic structure of
    # the library isn't leaked via directory names.
    accessible_subdirs: set[str] = set()
    if not _is_admin(current_user):
        prefix = f"{path.rstrip('/')}/" if path else ""
        rows = _accessible_locations_query(db, prefix, current_user).all()
        if path and not rows:
            raise HTTPException(status_code=403, detail="Forbidden")
        for (sp,) in rows:
            rest = sp[len(prefix):]
            if "/" in rest:
                accessible_subdirs.add(rest.split("/", 1)[0])

    items = []

    try:
        entries = sorted(os.listdir(target_dir))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    for entry in entries:
        if entry in _TOPDIR_SKIPLIST:
            continue

        entry_path = os.path.join(target_dir, entry)
        if not os.path.exists(entry_path):
            continue
        is_dir = os.path.isdir(entry_path)
        if is_dir and not _is_admin(current_user) and entry not in accessible_subdirs:
            continue

        try:
            size = os.path.getsize(entry_path) if not is_dir else 0
            mtime = datetime.fromtimestamp(os.path.getmtime(entry_path)).isoformat()
        except OSError:
            size = 0
            mtime = None

        item_data = {
            "name": entry,
            "is_dir": is_dir,
            "description": "",
            "cover_url": None,
            "size": size,
            "mtime": mtime,
            "path": os.path.relpath(entry_path, BOOKS_DIR).replace("\\", "/")
        }

        if os.path.islink(entry_path):
            file_hash = _resolve_vault_hash(entry_path)
            if file_hash:
                item_data["hash_id"] = file_hash
                cover_fs_path = os.path.join(BOOKS_DIR, ".data", "covers", f"{file_hash}.jpg")
                if os.path.exists(cover_fs_path):
                    item_data["cover_url"] = f"/api/covers/{file_hash}"
                book = db.query(models.Book).filter(models.Book.id == file_hash).first()
                if book:
                    if not _is_admin(current_user) and (book.clearance or 0) > _clearance_of(current_user):
                        continue
                    if book.title:
                        item_data["title"] = book.title
                    if book.author:
                        item_data["author"] = book.author
                    if book.description:
                        item_data["description"] = book.description
                    if book.publisher:
                        item_data["publisher"] = book.publisher
                    if book.published:
                        item_data["published"] = book.published
                    if book.tags:
                        item_data["tags"] = book.tags
                    if book.series:
                        item_data["series"] = book.series
                    if book.languages:
                        item_data["languages"] = book.languages
                    if book.identifiers:
                        item_data["identifiers"] = book.identifiers
                    item_data["clearance"] = int(book.clearance or 0)
                    item_data["import_date"] = book.import_date
                    if _is_admin(current_user):
                        item_data["last_verified_at"] = book.last_verified_at
                        item_data["last_verified_ok"] = book.last_verified_ok
                        item_data["last_verified_mode"] = book.last_verified_mode
                        item_data["last_verified_error"] = book.last_verified_error

        items.append(item_data)

    # Sort: folders first, then files
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    # Attach average ratings in one GROUP BY over the already-filtered items.
    stats = _rating_stats(db, [it.get("hash_id") for it in items])
    for it in items:
        s = stats.get(it.get("hash_id"))
        it["avg_rating"] = s["avg_rating"] if s else None
        it["rating_count"] = s["rating_count"] if s else 0

    return {"path": path, "items": items}

# --- Intelligent search -----------------------------------------------------
#
# A query is a sequence of tokens. Every positive token must match somewhere
# (AND across tokens); each token is matched across all searchable columns
# (OR across columns). A token may be quoted (contiguous phrase), prefixed with
# `-` (exclusion), or scoped to one column (`author:harnum`). The structural
# keys `path:`/`ext:`/`needs_review:` keep their old filter semantics.

# Columns searched when a token has no explicit field scope.
_SEARCH_ALL_COLUMNS = [
    models.Book.title, models.Book.author, models.Book.description,
    models.Book.publisher, models.Book.series, models.Book.tags,
    models.Book.original_filename,
]
# Field scopes the user may name explicitly (`tag` is an alias for `tags`).
_SEARCH_FIELD_COLUMNS = {
    "title": models.Book.title,
    "author": models.Book.author,
    "description": models.Book.description,
    "publisher": models.Book.publisher,
    "series": models.Book.series,
    "tags": models.Book.tags,
}
_SEARCH_FIELD_ALIASES = {"tag": "tags"}
_STRUCTURAL_KEYS = {"path", "ext", "needs_review"}
# Relevance weight of a scoped-field match (used by _relevance_score).
_SEARCH_FIELD_WEIGHT = {
    "title": 4, "author": 3, "publisher": 2, "series": 2, "tags": 2,
    "description": 1,
}

_SEARCH_TOKEN_RE = re.compile(r'''
    (?P<neg>-)?                          # optional exclusion marker
    (?:(?P<field>[A-Za-z_]+):)?          # optional field scope
    (?:
        "(?P<dq>[^"]*)"                  # double-quoted phrase
      | '(?P<sq>[^']*)'                  # single-quoted phrase
      | (?P<word>[^\s"']+)               # bare word
    )
''', re.VERBOSE)


def parse_search_query(q: str):
    """Tokenize a raw query string.

    Returns ``(terms, filters)`` where ``filters`` holds the structural
    path/ext/needs_review filters and ``terms`` is a list of dicts:
    ``{text, field, negate, is_phrase}`` (text is lowercased)."""
    filters = {"path": None, "ext": None, "needs_review": None}
    terms = []

    for m in _SEARCH_TOKEN_RE.finditer(q or ""):
        negate = m.group("neg") is not None
        field = (m.group("field") or "").lower()
        quoted = m.group("dq") is not None or m.group("sq") is not None
        value = m.group("dq")
        if value is None:
            value = m.group("sq")
        if value is None:
            value = m.group("word") or ""

        if field in _STRUCTURAL_KEYS:
            val = value.strip().lower()
            if not val:
                continue
            if field == "path":
                filters["path"] = val
            elif field == "ext":
                filters["ext"] = val if val.startswith(".") else "." + val
            elif field == "needs_review":
                if val in ("1", "true", "yes"):
                    filters["needs_review"] = True
                elif val in ("0", "false", "no"):
                    filters["needs_review"] = False
            continue

        scope = _SEARCH_FIELD_ALIASES.get(field, field)
        if scope not in _SEARCH_FIELD_COLUMNS:
            # Unknown `field:` prefix — treat the whole thing as literal text.
            scope = None
            if field:
                value = m.group("field") + ":" + value

        text = value.strip().lower()
        if not text:
            continue
        terms.append({
            "text": text,
            "field": scope,
            "negate": negate,
            "is_phrase": quoted,
        })

    return terms, filters


def _escape_like(text: str) -> str:
    """Escape SQL LIKE metacharacters in user text (used with ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _wildcard_escape(text: str) -> str:
    """Escape LIKE metacharacters, then map the user wildcards `*`/`?` to the
    SQL wildcards `%`/`_`."""
    return _escape_like(text).replace("*", "%").replace("?", "_")


def _like_pattern(text: str, is_phrase: bool) -> str:
    """Build a `%…%` LIKE pattern. SQL metacharacters in user text are escaped
    (ESCAPE '\\'); for non-phrase tokens the user wildcards `*`/`?` are then
    translated to SQL `%`/`_`."""
    esc = _escape_like(text) if is_phrase else _wildcard_escape(text)
    return f"%{esc}%"


def _col_expr(col):
    """Case-folded, NULL-safe column expression for LIKE matching."""
    return func.lower(func.coalesce(col, ""))


def _term_condition(term: dict):
    """SQL predicate for a single (positive) search term."""
    pattern = _like_pattern(term["text"], term["is_phrase"])
    if term["field"]:
        col = _SEARCH_FIELD_COLUMNS[term["field"]]
        return _col_expr(col).like(pattern, escape="\\")
    return or_(*[
        _col_expr(c).like(pattern, escape="\\") for c in _SEARCH_ALL_COLUMNS
    ])


def _relevance_score(terms: list):
    """Build an ORDER-BY relevance expression, or None if there is nothing to
    rank (no positive terms)."""
    positive = [t for t in terms if not t["negate"]]
    if not positive:
        return None

    score = None
    for t in positive:
        pattern = _like_pattern(t["text"], t["is_phrase"])
        if t["field"]:
            col = _SEARCH_FIELD_COLUMNS[t["field"]]
            term_score = case(
                (_col_expr(col).like(pattern, escape="\\"),
                 _SEARCH_FIELD_WEIGHT.get(t["field"], 1)),
                else_=0,
            )
        else:
            term_score = case(
                (_col_expr(models.Book.title).like(pattern, escape="\\"), 4),
                (_col_expr(models.Book.author).like(pattern, escape="\\"), 3),
                (or_(
                    _col_expr(models.Book.publisher).like(pattern, escape="\\"),
                    _col_expr(models.Book.series).like(pattern, escape="\\"),
                    _col_expr(models.Book.tags).like(pattern, escape="\\"),
                ), 2),
                (_col_expr(models.Book.description).like(pattern, escape="\\"), 1),
                else_=0,
            )
        score = term_score if score is None else score + term_score

    # Bonus when the whole plain-text query appears contiguously in the title.
    full = " ".join(
        t["text"] for t in positive if t["field"] is None and not t["is_phrase"]
    )
    if full:
        bonus_pat = _like_pattern(full, True)
        score = score + case(
            (_col_expr(models.Book.title).like(bonus_pat, escape="\\"), 50),
            else_=0,
        )
    return score


def _build_search_query(q: str, current_user: models.User | None, db: Session):
    """Shared query builder for /api/search and /api/search/hash_ids.
    Returns ``(query, terms)`` — the joined Book+BookLocation query with all
    filters applied, plus the parsed terms (for relevance ranking)."""
    terms, filters = parse_search_query(q)

    query = db.query(models.Book, models.BookLocation).join(
        models.BookLocation, models.Book.id == models.BookLocation.hash_id
    )

    if not _is_admin(current_user):
        query = query.filter(models.Book.clearance <= _clearance_of(current_user))

    conds = []
    for t in terms:
        cond = _term_condition(t)
        conds.append(not_(cond) if t["negate"] else cond)
    if conds:
        query = query.filter(and_(*conds))

    if filters["path"]:
        # `*`/`?` in the path act as wildcards (e.g. path:Law/*2024).
        pat = _wildcard_escape(filters["path"])
        query = query.filter(
            func.lower(models.BookLocation.symlink_path).like(f"{pat}%", escape="\\")
        )

    if filters["ext"]:
        # parse_search_query normalizes ext to start with a dot; `*`/`?` are
        # wildcards, so `ext:*` matches every file with an extension.
        pat = _wildcard_escape(filters["ext"])
        query = query.filter(
            func.lower(models.BookLocation.symlink_path).like(f"%{pat}", escape="\\")
        )

    if filters["needs_review"] is not None and _is_admin(current_user):
        query = query.filter(models.Book.needs_review == filters["needs_review"])

    return query, terms


@app.get("/api/search")
async def search(
    q: str = "",
    page: int = 1,
    per_page: int = 50,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))

    if not q:
        return {"matches": [], "page": page, "per_page": per_page, "total": 0, "total_pages": 0}

    query, terms = _build_search_query(q, current_user, db)

    total = query.order_by(None).count()
    total_pages = (total + per_page - 1) // per_page

    # Order by relevance, then average rating, then a stable tiebreak.
    avg_rating = (
        select(func.avg(models.BookRating.rating))
        .where(models.BookRating.hash_id == models.Book.id)
        .correlate(models.Book)
        .scalar_subquery()
    )
    order_cols = []
    score = _relevance_score(terms)
    if score is not None:
        order_cols.append(score.desc())
    order_cols += [
        avg_rating.desc().nulls_last(),
        models.Book.title, models.Book.id, models.BookLocation.symlink_path,
    ]

    results = (
        query.order_by(*order_cols)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    matches = []
    for book, loc in results:
        sym_path = loc.symlink_path
        cover_fs_path = os.path.join(BOOKS_DIR, ".data", "covers", f"{book.id}.jpg")
        cover_url = f"/api/covers/{book.id}" if os.path.exists(cover_fs_path) else None
        matches.append({
            "name": os.path.basename(sym_path),
            "is_dir": False,
            "path": sym_path,
            "parent_dir": os.path.dirname(sym_path),
            "cover_url": cover_url,
            "hash_id": book.id,
            "title": book.title,
            "author": book.author,
            "description": book.description,
            "clearance": int(book.clearance or 0),
        })

    stats = _rating_stats(db, [m["hash_id"] for m in matches])
    for m in matches:
        s = stats.get(m["hash_id"])
        m["avg_rating"] = s["avg_rating"] if s else None
        m["rating_count"] = s["rating_count"] if s else 0

    return {
        "matches": matches,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }


@app.get("/api/search/hash_ids")
async def search_hash_ids(
    q: str = "",
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all distinct hash_ids matching the search query, across all pages.
    Used by the admin bulk-verify 'Select All' action."""
    if not q:
        return {"hash_ids": [], "total": 0}
    query, _ = _build_search_query(q, current_user, db)
    rows = query.with_entities(models.Book.id).distinct().all()
    ids = [r[0] for r in rows]
    return {"hash_ids": ids, "total": len(ids)}


@app.post("/api/register", response_model=schemas.Message)
async def register_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    # Check if user already exists
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Check if a pending request already exists
    db_request = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.email == user.email).first()
    if db_request:
        raise HTTPException(status_code=400, detail="Registration request already pending")

    db_req = models.RegistrationRequest(
        email=user.email,
        source=user.source,
        purpose=user.purpose,
        status="pending"
    )
    db.add(db_req)
    db.commit()
    db.refresh(db_req)

    # Notify admin
    email_utils.send_admin_notification(
        user_email=db_req.email,
        token=db_req.token,
        source=db_req.source,
        purpose=db_req.purpose
    )

    return {"message": "Registration request queued for approval."}

@app.get("/api/admin/approve")
async def approve_user(token: str, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == token).first()
    if not db_req:
        return JSONResponse(status_code=404, content={"message": "Invalid or expired token."})

    if db_req.status == "approved":
        return JSONResponse(status_code=200, content={"message": "User already approved, waiting for password setup."})

    db_req.status = "approved"
    db.commit()

    # Notify user to set password
    email_utils.send_user_approval(db_req.email, db_req.token)

    return JSONResponse(status_code=200, content={"message": "User approved successfully. Email sent to set password."})

@app.post("/api/set-password", response_model=schemas.Message)
async def set_password(data: schemas.UserSetPassword, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == data.token).first()
    if not db_req or db_req.status != "approved":
        raise HTTPException(status_code=400, detail="Invalid or unapproved token.")

    # Create active user
    hashed_password = get_password_hash(data.password)
    real_name = (data.real_name or "").strip()[:100] or None
    new_user = models.User(
        email=db_req.email,
        hashed_password=hashed_password,
        real_name=real_name,
        is_active=True
    )
    db.add(new_user)
    db.delete(db_req)
    db.commit()

    return {"message": "Password set successfully. You can now log in."}

@app.get("/api/admin/reject")
async def reject_user(token: str, db: Session = Depends(get_db)):
    db_req = db.query(models.RegistrationRequest).filter(models.RegistrationRequest.token == token).first()
    if not db_req:
        return JSONResponse(status_code=404, content={"message": "Invalid or expired token."})

    user_email = db_req.email
    db.delete(db_req)
    db.commit()

    # Notify user
    email_utils.send_user_rejection(user_email)

    return JSONResponse(status_code=200, content={"message": "User rejected successfully."})


# ---------------- Admin: clearance management ----------------

@app.get("/api/admin/users", response_model=List[schemas.AdminUserSummary])
async def admin_list_users(
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return db.query(models.User).order_by(models.User.email).all()


@app.put("/api/admin/users/{user_id}/clearance", response_model=schemas.AdminUserSummary)
async def admin_set_user_clearance(
    user_id: int,
    payload: schemas.UserClearanceUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.clearance is not None:
        if payload.clearance < 0:
            raise HTTPException(status_code=400, detail="Clearance must be non-negative")
        user.clearance = payload.clearance
    if payload.is_admin is not None:
        # Guard: don't let an admin demote themselves into having zero admins.
        if user.id == admin.id and payload.is_admin is False:
            raise HTTPException(status_code=400, detail="Refusing to demote the current admin")
        user.is_admin = payload.is_admin
    if payload.is_active is not None:
        # Guard: an admin must not lock themselves out of the system.
        if user.id == admin.id and payload.is_active is False:
            raise HTTPException(status_code=400, detail="Refusing to deactivate the current admin")
        user.is_active = payload.is_active
    db.commit()
    db.refresh(user)
    return user


@app.put("/api/admin/books/{hash_id}/clearance")
async def admin_set_book_clearance(
    hash_id: str,
    payload: schemas.BookClearanceUpdate,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.clearance < 0:
        raise HTTPException(status_code=400, detail="Clearance must be non-negative")
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    book.clearance = payload.clearance
    db.commit()
    return {"hash_id": hash_id, "clearance": book.clearance}


@app.post("/api/admin/books/clearance")
async def admin_bulk_set_book_clearance(
    payload: schemas.BulkBookClearanceUpdate,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.clearance < 0:
        raise HTTPException(status_code=400, detail="Clearance must be non-negative")
    if not payload.hash_ids:
        return {"updated": 0, "clearance": payload.clearance}
    updated = db.query(models.Book).filter(models.Book.id.in_(payload.hash_ids)).update(
        {models.Book.clearance: payload.clearance},
        synchronize_session=False,
    )
    db.commit()
    return {"updated": updated, "clearance": payload.clearance}


def _cover_url_for(hash_id: str) -> Optional[str]:
    """Return a cache-busted URL for the book's cover if the JPEG exists in
    the vault, else None."""
    cover_path = os.path.join(DATA_DIR, "covers", f"{hash_id}.jpg")
    try:
        mtime = int(os.path.getmtime(cover_path))
    except OSError:
        return None
    return f"/api/covers/{hash_id}?v={mtime}"


def _book_to_admin_detail(book: models.Book, db: Session) -> dict:
    locs = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == book.id
    ).all()
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "publisher": book.publisher,
        "published": book.published,
        "description": book.description,
        "tags": book.tags,
        "series": book.series,
        "languages": book.languages,
        "identifiers": book.identifiers,
        "original_filename": book.original_filename,
        "clearance": int(book.clearance or 0),
        "needs_review": bool(book.needs_review),
        "locations": [r[0] for r in locs],
        "cover_url": _cover_url_for(book.id),
        "last_verified_at": book.last_verified_at,
        "last_verified_ok": book.last_verified_ok,
        "last_verified_mode": book.last_verified_mode,
        "last_verified_error": book.last_verified_error,
    }


@app.get("/api/admin/books/{hash_id}", response_model=schemas.AdminBookDetail)
async def admin_get_book(
    hash_id: str,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return _book_to_admin_detail(book, db)


@app.put("/api/admin/books/{hash_id}", response_model=schemas.AdminBookDetail)
async def admin_update_book(
    hash_id: str,
    payload: schemas.BookUpdate,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    updates = payload.model_dump(exclude_unset=True)
    if "clearance" in updates and updates["clearance"] is not None and updates["clearance"] < 0:
        raise HTTPException(status_code=400, detail="Clearance must be non-negative")
    for field, val in updates.items():
        setattr(book, field, val)
    db.commit()
    db.refresh(book)
    return _book_to_admin_detail(book, db)


@app.delete("/api/admin/books/{hash_id}")
async def admin_delete_book(
    hash_id: str,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    locations = [r[0] for r in db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == hash_id
    ).all()]

    # Filesystem cleanup. Tolerate missing files — best-effort.
    errors = []
    books_root = os.path.abspath(BOOKS_DIR)
    for sp in locations:
        full = os.path.abspath(os.path.join(BOOKS_DIR, sp))
        # Refuse to touch anything that escapes BOOKS_DIR or that isn't a symlink.
        if not full.startswith(books_root + os.sep):
            errors.append(f"refused traversal: {sp}")
            continue
        if os.path.islink(full):
            try:
                os.remove(full)
            except OSError as e:
                errors.append(f"symlink {sp}: {e}")

    vault_file = os.path.join(BOOKS_DIR, ".data", hash_id)
    if os.path.exists(vault_file):
        try:
            os.remove(vault_file)
        except OSError as e:
            errors.append(f"vault: {e}")
    cover_file = os.path.join(BOOKS_DIR, ".data", "covers", f"{hash_id}.jpg")
    if os.path.exists(cover_file):
        try:
            os.remove(cover_file)
        except OSError as e:
            errors.append(f"cover: {e}")

    # Foreign keys in SQLite aren't enforced unless PRAGMA foreign_keys=ON,
    # which we don't set — explicitly clear referencing rows so we don't leave
    # orphans in favorites / reading_progress / book_locations.
    db.query(models.BookLocation).filter(models.BookLocation.hash_id == hash_id).delete()
    db.query(models.Favorite).filter(models.Favorite.hash_id == hash_id).delete()
    db.query(models.ReadingProgress).filter(models.ReadingProgress.hash_id == hash_id).delete()
    db.query(models.BookRating).filter(models.BookRating.hash_id == hash_id).delete()
    db.query(models.BookComment).filter(models.BookComment.hash_id == hash_id).delete()
    db.delete(book)
    db.commit()
    return {"deleted": hash_id, "locations": locations, "errors": errors}


# ---------- Admin: move book(s) between locations ----------


def _rel_under_books(p: str) -> str:
    """Normalise an input path to a relative POSIX path under BOOKS_DIR.
    Strips leading/trailing slashes, rejects empty + traversal. Mirrors the
    inline guard used by the commit endpoint at main.py:1496-1498."""
    rel = (p or "").strip().lstrip("/").rstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="Empty path")
    abs_p = os.path.abspath(os.path.join(BOOKS_DIR, rel))
    books_abs = os.path.abspath(BOOKS_DIR)
    if not (abs_p == books_abs or abs_p.startswith(books_abs + os.sep)):
        raise HTTPException(status_code=400, detail="Path escapes library root")
    return os.path.relpath(abs_p, books_abs).replace("\\", "/")


def _rmdir_empty_upwards(start_abs: str) -> None:
    """rmdir start_abs and every empty parent directory up to (but not
    including) BOOKS_DIR. Tolerant — stops at first non-empty dir or OSError."""
    books_abs = os.path.abspath(BOOKS_DIR)
    cur = os.path.abspath(start_abs)
    while cur.startswith(books_abs + os.sep) and cur != books_abs:
        try:
            os.rmdir(cur)
        except OSError:
            return
        cur = os.path.dirname(cur)


@app.post("/api/admin/move", response_model=schemas.MoveResponse)
async def admin_move(
    payload: schemas.MoveRequest,
    dry_run: bool = False,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Move a managed book (symlink) or an entire directory subtree to a new
    location. The hash never changes — only `book_locations.symlink_path` and
    the symlink on disk are updated. Favourites and reading progress reference
    by `hash_id`, so they survive the move automatically."""
    src = _rel_under_books(payload.src)
    dst = _rel_under_books(payload.dst)

    if src == dst:
        return schemas.MoveResponse(
            src=src, dst=dst, kind="file", dry_run=dry_run,
            moved=[], errors=[],
            skipped=[{"path": src, "reason": "from == to"}],
        )

    # A move that lands inside its own subtree (Urantia → Urantia/Foo) would
    # corrupt the prefix-rename invariant. Reject up front.
    if (dst + "/").startswith(src + "/"):
        raise HTTPException(status_code=400, detail="Cannot move into a subdirectory of itself")

    books_abs = os.path.abspath(BOOKS_DIR)
    src_abs = os.path.join(books_abs, src)

    if not os.path.lexists(src_abs):
        raise HTTPException(status_code=404, detail="Source not found")

    # Decide file vs directory. "File" here means a symlink into the CAS vault
    # (i.e. a managed book). Anything else is rejected.
    if os.path.islink(src_abs):
        kind = "file"
        rows = (
            db.query(models.BookLocation)
            .filter(models.BookLocation.symlink_path == src)
            .all()
        )
        if not rows:
            raise HTTPException(status_code=400, detail="Not a managed book")
        moves: list[tuple[str, str, str]] = [(src, dst, rows[0].hash_id)]
    elif os.path.isdir(src_abs):
        kind = "directory"
        like_prefix = src + "/"
        rows = (
            db.query(models.BookLocation)
            .filter(models.BookLocation.symlink_path.like(like_prefix + "%"))
            .all()
        )
        if not rows:
            raise HTTPException(status_code=404, detail="No registered books under this directory")
        moves = [(r.symlink_path, dst + r.symlink_path[len(src):], r.hash_id) for r in rows]
    else:
        raise HTTPException(status_code=400, detail="Source is neither a symlink nor a directory")

    # Pre-flight: every destination must be free. All-or-nothing on collision.
    collisions = []
    for (_old, new_rel, _h) in moves:
        new_abs = os.path.join(books_abs, new_rel)
        if os.path.lexists(new_abs):
            collisions.append({"path": new_rel, "reason": "destination exists"})
    if collisions:
        return schemas.MoveResponse(
            src=src, dst=dst, kind=kind, dry_run=dry_run,
            moved=[], errors=collisions, skipped=[],
        )

    if dry_run:
        return schemas.MoveResponse(
            src=src, dst=dst, kind=kind, dry_run=True,
            moved=[schemas.MoveItem(src=o, dst=n, hash_id=h) for (o, n, h) in moves],
            errors=[], skipped=[],
        )

    moved: list[schemas.MoveItem] = []
    errors: list[dict] = []

    for (old_rel, new_rel, hash_id) in moves:
        old_abs = os.path.join(books_abs, old_rel)
        new_abs = os.path.join(books_abs, new_rel)
        new_parent = os.path.dirname(new_abs)

        # Re-derive a relative symlink target from the NEW parent — the old
        # symlink's relative target is invalid from a different parent.
        # Mirrors the commit endpoint's pattern (main.py:1534-1535).
        try:
            os.makedirs(new_parent, exist_ok=True)
            vault_path = os.path.join(DATA_DIR, hash_id)
            new_target = os.path.relpath(vault_path, new_parent)
        except OSError as e:
            errors.append({"path": old_rel, "reason": f"mkdir failed: {e}"})
            continue

        # (a) create new symlink
        try:
            os.symlink(new_target, new_abs)
        except FileExistsError:
            errors.append({"path": new_rel, "reason": "race: destination appeared"})
            continue
        except OSError as e:
            errors.append({"path": old_rel, "reason": f"symlink failed: {e}"})
            continue

        # (b) update DB
        try:
            db.query(models.BookLocation).filter(
                models.BookLocation.symlink_path == old_rel
            ).update(
                {models.BookLocation.symlink_path: new_rel},
                synchronize_session=False,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            # (c) rollback step (a)
            try:
                os.remove(new_abs)
            except OSError:
                pass
            errors.append({"path": old_rel, "reason": f"DB update failed: {e}"})
            continue

        # (d) best-effort removal of the old symlink. If this fails the DB is
        # already authoritative — leaves a stale symlink behind that the user
        # can clean up later. Log for visibility.
        try:
            os.remove(old_abs)
        except OSError as ex:
            logging.warning("admin_move: stale old symlink left behind: %s (%s)", old_rel, ex)

        moved.append(schemas.MoveItem(src=old_rel, dst=new_rel, hash_id=hash_id))

    # For directory moves, sweep the now-likely-empty source subtree, then
    # walk up removing empty parents until we hit a non-empty dir or BOOKS_DIR.
    if kind == "directory" and os.path.isdir(src_abs):
        for root, _dirs, _files in os.walk(src_abs, topdown=False):
            try:
                os.rmdir(root)
            except OSError:
                pass
        _rmdir_empty_upwards(os.path.dirname(src_abs))

    return schemas.MoveResponse(
        src=src, dst=dst, kind=kind, dry_run=False,
        moved=moved, errors=errors, skipped=[],
    )


# ---------- Admin upload (Add Book wizard) ----------

STAGING_DIR = os.path.join(DATA_DIR, "staging")
COVERS_DIR = os.path.join(DATA_DIR, "covers")


def _ensure_writable_dir(path: str) -> None:
    """Lazy mkdir — module-load shouldn't fail on a read-only FS just because
    a dir doesn't exist yet; surface a clean 500 only at first actual use."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot create {path}: {e}. Service needs write access "
                   f"(extend ReadWritePaths= in urantia-library.service).",
        )

_STAGING: dict[str, dict] = {}
_STAGING_LOCK = threading.Lock()
_STAGING_TTL_S = 3600
_MAX_UPLOAD_BYTES = 850 * 1024 * 1024
_MAX_COVER_BYTES = 5 * 1024 * 1024

_ACCEPTED_BOOK_EXTS = {
    "fb2", "zip", "epub", "pdf", "djvu", "mobi", "azw", "azw3", "prc",
    "docx", "odt", "html", "rtf", "txt", "jpg", "jpeg",
}



def _purge_expired_staging() -> None:
    now = datetime.now(timezone.utc).timestamp()
    with _STAGING_LOCK:
        expired = [sid for sid, rec in _STAGING.items() if rec.get("expires_at", 0) < now]
        for sid in expired:
            rec = _STAGING.pop(sid, None)
            if rec:
                shutil.rmtree(rec.get("dir", ""), ignore_errors=True)


def _detect_format(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".fb2.zip"):
        return "fb2.zip"
    if name.endswith(".txt.zip"):
        return "txt.zip"
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return ext


def _zip_fb2_inplace(src_path: str) -> str:
    """Compress a freshly-uploaded .fb2 into a deterministic .fb2.zip and
    delete the original. Returns the new (.zip) path. Determinism — fixed
    date_time, fixed external_attr, fixed compression — makes the hash of the
    zip stable so duplicate detection works for repeat uploads."""
    dst = src_path + ".zip"
    inner_name = os.path.basename(src_path)
    info = zipfile.ZipInfo(filename=inner_name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    with open(src_path, "rb") as fin, zipfile.ZipFile(dst, "w") as zf:
        with zf.open(info, "w", force_zip64=True) as zout:
            while True:
                chunk = fin.read(1024 * 1024)
                if not chunk:
                    break
                zout.write(chunk)
    os.remove(src_path)
    return dst


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _extract_upload_metadata(src_path: str, fmt: str) -> dict:
    """Port of the metadata extraction tree from migrate_library.py. Returns a
    dict with keys matching the Book columns (description maps from annotation
    so callers can drop it straight into BookUpdate). Named '_upload_' to avoid
    collision with the FB2-specific _extract_metadata defined later in main.py."""
    out = {
        "title": None, "author": None, "publisher": None, "published": None,
        "description": None, "tags": None, "series": None, "languages": None,
        "identifiers": None,
    }
    try:
        if fmt == "pdf":
            r = _run(["pdfinfo", src_path], timeout=10)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if line.startswith("Title:"):
                        v = line.split(":", 1)[1].strip()
                        if v:
                            out["title"] = v
                    elif line.startswith("Author:"):
                        v = line.split(":", 1)[1].strip()
                        if v:
                            out["author"] = v
        elif fmt == "djvu":
            r = _run(["djvused", "-e", "print-meta", src_path], timeout=10)
            if r.returncode == 0:
                import ast as _ast
                for line in r.stdout.splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) != 2:
                        continue
                    key = parts[0].strip().lower()
                    raw_val = parts[1].strip()
                    try:
                        val = _ast.literal_eval("b" + raw_val).decode("utf-8").strip()
                    except Exception:
                        val = raw_val.strip('" ')
                    if not val:
                        continue
                    if key == "title": out["title"] = val
                    elif key == "author": out["author"] = val
                    elif key == "publisher": out["publisher"] = val
                    elif key == "year": out["published"] = val
                    elif key == "keywords": out["tags"] = val
                    elif key == "descr": out["description"] = val
                    elif key == "isbn":
                        out["identifiers"] = val if val.startswith("isbn:") else f"isbn:{val}"
                    elif key == "lang": out["languages"] = val.lower()
        else:
            # ebook-meta dispatches on extension; ensure src_path keeps its name.
            r = _run(["ebook-meta", src_path], timeout=15)
            if r.returncode == 0:
                current_key = None
                comments_buffer: list[str] = []
                for line in r.stdout.splitlines():
                    m = re.match(r"^([A-Za-z\(\)]+)\s*:\s*(.*)", line)
                    if m:
                        raw_key, val = m.groups()
                        key = raw_key.strip().lower()
                        val = val.strip()
                        if val == "Unknown":
                            val = ""
                        if key == "title" and val: out["title"] = val
                        elif key == "author(s)" and val: out["author"] = re.sub(r"\[.*?\]", "", val).strip()
                        elif key == "publisher" and val: out["publisher"] = val
                        elif key == "tags" and val: out["tags"] = val
                        elif key == "series" and val: out["series"] = val
                        elif key == "languages" and val: out["languages"] = val.lower()
                        elif key == "published" and val: out["published"] = val
                        elif key == "identifiers" and val: out["identifiers"] = val
                        elif key == "comments":
                            current_key = "comments"
                            if val:
                                comments_buffer.append(val)
                        else:
                            current_key = None
                    elif current_key == "comments":
                        comments_buffer.append(line.strip())
                if comments_buffer:
                    out["description"] = "\n".join(comments_buffer).strip()
    except Exception as e:
        logging.warning("metadata extraction failed for %s: %s", src_path, e)
    return out


def _extract_cover_to(src_path: str, fmt: str, dest_jpg: str) -> Optional[tuple[int, int]]:
    """Run the format-appropriate cover extraction, resize to 300px-wide JPEG
    written at dest_jpg. Returns (width, height) of the saved cover, or None on
    failure."""
    tmp_dir = os.path.join(STAGING_DIR, f".cover-{uuid.uuid4().hex}")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        raw = None
        if fmt == "djvu":
            raw = os.path.join(tmp_dir, "cover.ppm")
            r = _run(["ddjvu", "-format=ppm", "-pages=1", src_path, raw], timeout=30)
            if r.returncode != 0 or not os.path.exists(raw):
                return None
        elif fmt == "pdf":
            r = _run(["pdftoppm", "-singlefile", src_path, os.path.join(tmp_dir, "cover")], timeout=30)
            if r.returncode != 0:
                return None
            for cand in ("cover.ppm", "cover.jpg", "cover.png"):
                p = os.path.join(tmp_dir, cand)
                if os.path.exists(p):
                    raw = p
                    break
            if raw is None:
                return None
        elif fmt in ("jpg", "jpeg", "png", "webp"):
            # The "book" file IS the image — use it directly as the cover source.
            raw = src_path
        elif fmt in ("txt", "txt.zip", "rtf", "html"):
            # No reliable cover for these — ebook-meta would either fail or
            # produce nothing useful. Skip cleanly so the upload continues
            # without a cover (the UI shows the striped placeholder).
            return None
        else:
            raw = os.path.join(tmp_dir, "cover.jpg")
            r = _run(["ebook-meta", src_path, f"--get-cover={raw}"], timeout=30)
            if r.returncode != 0 or not os.path.exists(raw):
                return None
        with Image.open(raw) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w > 300:
                new_h = max(1, int(h * 300 / w))
                im = im.resize((300, new_h), Image.LANCZOS)
                w, h = im.size
            os.makedirs(os.path.dirname(dest_jpg), exist_ok=True)
            im.save(dest_jpg, format="JPEG", quality=85)
            return (w, h)
    except Exception as e:
        logging.warning("cover extraction failed for %s: %s", src_path, e)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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


def _like_escape(s: str) -> str:
    """Escape SQL LIKE metacharacters so a literal path can be used as a
    prefix pattern. A directory named e.g. `Law_2024` must not match
    `LawX2024` via the `_` wildcard."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@app.delete("/api/admin/dirs")
def admin_delete_dir(
    path: str,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Recursively delete a directory, cleaning up symlinks, book database entries,
    and orphan vault/cover files.

    Destructive steps are ordered so nothing irreversible happens until success
    is guaranteed: the DB is queried read-only, then `rmtree` runs (its failure
    leaves the DB and vault untouched), then DB rows are deleted and committed,
    and only after the commit do orphaned vault/cover files get removed."""
    sub = _safe_subpath(path)
    if not sub:
        raise HTTPException(status_code=400, detail="Cannot delete root directory")

    target_dir = os.path.abspath(os.path.join(BOOKS_DIR, sub))
    books_root = os.path.abspath(BOOKS_DIR)

    if not target_dir.startswith(books_root + os.sep) or target_dir == books_root:
        raise HTTPException(status_code=403, detail="Forbidden")

    if os.path.islink(target_dir):
        raise HTTPException(status_code=400, detail="Path is a symlink, not a directory")

    if not os.path.exists(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="Path is not a directory")

    # The DB — not the filesystem — is the canonical record of book locations.
    # One indexed prefix scan finds every location under the directory; the
    # exact `startswith` refine guards against LIKE wildcard over-matching.
    prefix = f"{sub}/"
    like_prefix = _like_escape(prefix) + "%"
    locs = [
        r for r in db.query(models.BookLocation)
        .filter(models.BookLocation.symlink_path.like(like_prefix, escape="\\"))
        .all()
        if r.symlink_path.startswith(prefix)
    ]

    # A hash is orphaned iff it has no location left outside the deleted subtree.
    inside_hashes = {r.hash_id for r in locs}
    orphan_hashes = []
    for h in inside_hashes:
        other = (
            db.query(models.BookLocation.symlink_path)
            .filter(models.BookLocation.hash_id == h)
            .all()
        )
        if all(sp[0].startswith(prefix) for sp in other):
            orphan_hashes.append(h)

    # rmtree first: on failure the DB and vault are still pristine.
    try:
        shutil.rmtree(target_dir)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove directory: {e}")

    # DB cleanup. SQLite foreign keys aren't enforced, so referencing rows are
    # cleared explicitly (mirrors admin_delete_book).
    for loc in locs:
        db.delete(loc)
    for h in orphan_hashes:
        book = db.query(models.Book).filter(models.Book.id == h).first()
        if book:
            db.delete(book)
    if orphan_hashes:
        db.query(models.Favorite).filter(
            models.Favorite.hash_id.in_(orphan_hashes)
        ).delete(synchronize_session=False)
        db.query(models.ReadingProgress).filter(
            models.ReadingProgress.hash_id.in_(orphan_hashes)
        ).delete(synchronize_session=False)
    # Directory bookmarks for the deleted directory and any subdirectory.
    db.query(models.DirectoryFavorite).filter(
        or_(
            models.DirectoryFavorite.path == sub,
            models.DirectoryFavorite.path.like(like_prefix, escape="\\"),
        )
    ).delete(synchronize_session=False)
    db.commit()

    # Only now — past the point of no rollback — remove orphan vault/cover
    # files. Best-effort: failures are reported but don't fail the request.
    errors = []
    for h in orphan_hashes:
        vault_file = os.path.join(BOOKS_DIR, ".data", h)
        if os.path.exists(vault_file):
            try:
                os.remove(vault_file)
            except OSError as e:
                errors.append(f"vault {h}: {e}")
        cover_file = os.path.join(BOOKS_DIR, ".data", "covers", f"{h}.jpg")
        if os.path.exists(cover_file):
            try:
                os.remove(cover_file)
            except OSError as e:
                errors.append(f"cover {h}: {e}")

    return {
        "deleted_directory": sub,
        "locations_removed": len(locs),
        "books_deleted": len(orphan_hashes),
        "errors": errors,
    }


@app.get("/api/admin/dirs", response_model=schemas.DirListing)
def admin_dirs(
    path: str = "",
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List immediate subdirectory names under /<path>/ — powers the
    expand-on-click directory tree in the upload view. Combines DB-known
    children with on-disk directories so empty-but-existing folders appear
    too. `path=""` lists root-level directories."""
    sub = _safe_subpath(path)
    prefix = f"{sub}/" if sub else ""
    dirs: set[str] = set()

    rows = (
        db.query(models.BookLocation.symlink_path)
        .filter(models.BookLocation.symlink_path.like(f"{prefix}%/%"))
        .all()
    )
    for (sp,) in rows:
        rest = sp[len(prefix):]
        first = rest.split("/", 1)[0] if "/" in rest else ""
        if first and not first.startswith(".") and (sub or first not in _TOPDIR_SKIPLIST):
            dirs.add(first)

    fs_dir = os.path.join(BOOKS_DIR, sub) if sub else BOOKS_DIR
    if os.path.isdir(fs_dir):
        try:
            for entry in os.listdir(fs_dir):
                if entry.startswith("."):
                    continue
                if not sub and entry in _TOPDIR_SKIPLIST:
                    continue
                if os.path.isdir(os.path.join(fs_dir, entry)):
                    dirs.add(entry)
        except OSError:
            pass
    return {"path": sub, "dirs": sorted(dirs)}


def _validate_cover_upload(file: UploadFile) -> None:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Invalid image file")


def _save_cover_for_hash(file: UploadFile, hash_id: str) -> str:
    """Resize uploaded image to 300px-wide JPEG, write to
    .data/covers/<hash>.jpg, return cache-busted URL."""
    _validate_cover_upload(file)
    raw = file.file.read(_MAX_COVER_BYTES + 1)
    if len(raw) > _MAX_COVER_BYTES:
        raise HTTPException(status_code=413, detail="Cover too large")
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Unreadable image")
    w, h = im.size
    if w > 300:
        new_h = max(1, int(h * 300 / w))
        im = im.resize((300, new_h), Image.LANCZOS)
    _ensure_writable_dir(COVERS_DIR)
    dest = os.path.join(COVERS_DIR, f"{hash_id}.jpg")
    im.save(dest, format="JPEG", quality=85)
    return _cover_url_for(hash_id) or f"/api/covers/{hash_id}"


@app.put("/api/admin/books/{hash_id}/cover", response_model=schemas.CoverUpdateResponse)
async def admin_replace_book_cover(
    hash_id: str,
    file: UploadFile = File(...),
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    cover_url = _save_cover_for_hash(file, hash_id)
    return {"cover_url": cover_url}


@app.post("/api/admin/books/{hash_id}/cover/reextract", response_model=schemas.CoverUpdateResponse)
async def admin_reextract_book_cover(
    hash_id: str,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    book = db.query(models.Book).filter(models.Book.id == hash_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    loc_row = db.query(models.BookLocation.symlink_path).filter(
        models.BookLocation.hash_id == hash_id
    ).first()
    if not loc_row:
        raise HTTPException(status_code=404, detail="Book has no registered location")
    symlink_fs = os.path.join(BOOKS_DIR, loc_row[0])
    real_path = os.path.realpath(symlink_fs)
    if not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Book file missing on disk")
    fmt = _detect_format(book.original_filename or loc_row[0])
    _ensure_writable_dir(COVERS_DIR)
    _ensure_writable_dir(STAGING_DIR)
    dest = os.path.join(COVERS_DIR, f"{hash_id}.jpg")
    # Run extraction against a copy that retains the original extension so
    # ebook-meta's dispatch works (the vault file is bare hex).
    tmp_dir = os.path.join(STAGING_DIR, f".reextract-{uuid.uuid4().hex}")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        tmp_src = os.path.join(tmp_dir, book.original_filename or "book.bin")
        try:
            os.symlink(real_path, tmp_src)
        except OSError:
            shutil.copy2(real_path, tmp_src)
        result = _extract_cover_to(tmp_src, fmt, dest)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if result is None:
        raise HTTPException(status_code=500, detail="Cover extraction failed")
    cover_url = _cover_url_for(hash_id) or f"/api/covers/{hash_id}"
    return {"cover_url": cover_url}


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _log_entry(level: str, msg: str) -> dict:
    return {
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}",
        "level": level,
        "msg": msg,
    }


def _format_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    i = 0
    f = float(n)
    while f >= 1024.0 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return f"{f:.1f} {units[i]}" if i else f"{int(f)} {units[i]}"


@app.post("/api/admin/books/upload")
async def admin_upload_book(
    file: UploadFile = File(...),
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Multipart upload of a single book file. Returns an SSE stream of `log`
    events ending with a `done` event whose payload either contains
    `extracted_metadata` + `staging_id` (success) or `existing` (duplicate)."""
    _purge_expired_staging()
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="No filename")
    fmt = _detect_format(filename)
    primary_ext = fmt.split(".")[-1]
    if primary_ext not in _ACCEPTED_BOOK_EXTS:
        raise HTTPException(status_code=415, detail=f"Unsupported format: {fmt}")
    owner_id = _admin.id

    # Pre-create the staging dir + buffer the upload to disk *before* we open
    # the generator, so that fatal write errors surface as a clean HTTP error
    # (not a half-streamed SSE response).
    _ensure_writable_dir(STAGING_DIR)
    staging_id = uuid.uuid4().hex
    sdir = os.path.join(STAGING_DIR, staging_id)
    os.makedirs(sdir, exist_ok=True)
    safe_name = os.path.basename(filename)
    dest_path = os.path.join(sdir, safe_name)
    bytes_written = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _MAX_UPLOAD_BYTES:
                    out.close()
                    shutil.rmtree(sdir, ignore_errors=True)
                    raise HTTPException(status_code=413, detail="File too large")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(sdir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    async def stream():
        # Mutable locals so the FB2→FB2.ZIP step can rewrite them mid-stream.
        nonlocal_fmt = fmt
        nonlocal_safe_name = safe_name
        nonlocal_dest_path = dest_path
        nonlocal_bytes = bytes_written
        try:
            size_str = _format_size(nonlocal_bytes)
            yield _sse_event("log", _log_entry("info", f"POST /api/admin/books/upload  multipart/form-data"))
            yield _sse_event("log", _log_entry("info", f'Receiving "{nonlocal_safe_name}" ({size_str})'))
            yield _sse_event("log", _log_entry("info", f"Upload complete — {nonlocal_bytes} bytes"))

            if nonlocal_fmt == "fb2":
                yield _sse_event("log", _log_entry("info", "Compressing FB2 → FB2.ZIP for storage…"))
                nonlocal_dest_path = await asyncio.to_thread(_zip_fb2_inplace, nonlocal_dest_path)
                nonlocal_safe_name = os.path.basename(nonlocal_dest_path)
                nonlocal_bytes = os.path.getsize(nonlocal_dest_path)
                nonlocal_fmt = "fb2.zip"
                yield _sse_event("log", _log_entry("ok", f"Stored as {nonlocal_safe_name} ({_format_size(nonlocal_bytes)})"))

            yield _sse_event("log", _log_entry("info", "Computing BLAKE2b…"))
            file_hash = await asyncio.to_thread(_blake2b_of_file, nonlocal_dest_path)
            yield _sse_event("log", _log_entry("ok", f"hash = {file_hash}"))

            yield _sse_event("log", _log_entry("info", "Checking registry for duplicates…"))
            existing = db.query(models.Book).filter(models.Book.id == file_hash).first()
            if existing:
                yield _sse_event("log", _log_entry("warn", "Hash collision in registry"))
                yield _sse_event("log", _log_entry("error", f"Duplicate: matches existing book id {file_hash[:12]}…"))
                shutil.rmtree(sdir, ignore_errors=True)
                yield _sse_event("done", {"existing": _book_to_admin_detail(existing, db)})
                return
            yield _sse_event("log", _log_entry("ok", "No duplicate found"))

            yield _sse_event("log", _log_entry("info", f"Detecting format → {nonlocal_fmt.upper()}"))

            yield _sse_event("log", _log_entry("info", "Running metadata extractor…"))
            metadata = await asyncio.to_thread(_extract_upload_metadata, nonlocal_dest_path, nonlocal_fmt)
            if metadata.get("title"):
                yield _sse_event("log", _log_entry("ok", f"Parsed {nonlocal_fmt} description block"))
            else:
                yield _sse_event("log", _log_entry("warn", "No title found — filename fallback will apply"))
                metadata["title"] = os.path.splitext(nonlocal_safe_name)[0].replace("-", " ").replace("_", " ")

            yield _sse_event("log", _log_entry("info", "Extracting cover image…"))
            cover_dest = os.path.join(sdir, "cover.jpg")
            cover_dims = await asyncio.to_thread(_extract_cover_to, nonlocal_dest_path, nonlocal_fmt, cover_dest)
            if cover_dims:
                w, h = cover_dims
                yield _sse_event("log", _log_entry("ok", f"Cover extracted ({w} × {h} JPEG)"))
                yield _sse_event("log", _log_entry("info", "Generating 300px thumbnail…"))
                yield _sse_event("log", _log_entry("ok", f"Thumbnail written to {os.path.basename(cover_dest)}"))
            else:
                yield _sse_event("log", _log_entry("warn", "Cover extraction failed — none saved"))

            with _STAGING_LOCK:
                _STAGING[staging_id] = {
                    "dir": sdir,
                    "filename": nonlocal_safe_name,
                    "hash": file_hash,
                    "size": nonlocal_bytes,
                    "format": nonlocal_fmt,
                    "metadata": metadata,
                    "cover_w": cover_dims[0] if cover_dims else None,
                    "cover_h": cover_dims[1] if cover_dims else None,
                    "expires_at": datetime.now(timezone.utc).timestamp() + _STAGING_TTL_S,
                    "owner_id": owner_id,
                }

            yield _sse_event("log", _log_entry("ok", "Ready for review"))

            payload = {
                "staging_id": staging_id,
                "hash": file_hash,
                "size": nonlocal_bytes,
                "format": nonlocal_fmt,
                "filename": nonlocal_safe_name,
                "cover_url": f"/api/admin/books/upload/{staging_id}/cover.jpg" if cover_dims else None,
                "extracted_metadata": metadata,
            }
            yield _sse_event("done", payload)
        except Exception as e:
            logging.exception("admin_upload_book stream failed")
            shutil.rmtree(sdir, ignore_errors=True)
            yield _sse_event("log", _log_entry("error", f"Internal error: {e}"))
            yield _sse_event("done", {"error": str(e)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.get("/api/admin/books/upload/{staging_id}/cover.jpg")
def admin_staging_cover(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    rec = _STAGING.get(staging_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Staging not found")
    cover = os.path.join(rec["dir"], "cover.jpg")
    if not os.path.exists(cover):
        raise HTTPException(status_code=404, detail="No cover for staging")
    return FileResponse(cover, media_type="image/jpeg")


@app.post("/api/admin/books/upload/{staging_id}/cover", response_model=schemas.CoverUpdateResponse)
async def admin_staging_cover_override(
    staging_id: str,
    file: UploadFile = File(...),
    _admin: models.User = Depends(require_admin),
):
    rec = _STAGING.get(staging_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Staging not found")
    _validate_cover_upload(file)
    raw = file.file.read(_MAX_COVER_BYTES + 1)
    if len(raw) > _MAX_COVER_BYTES:
        raise HTTPException(status_code=413, detail="Cover too large")
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Unreadable image")
    w, h = im.size
    if w > 300:
        new_h = max(1, int(h * 300 / w))
        im = im.resize((300, new_h), Image.LANCZOS)
        w, h = im.size
    dest = os.path.join(rec["dir"], "cover.jpg")
    im.save(dest, format="JPEG", quality=85)
    rec["cover_w"], rec["cover_h"] = w, h
    return {"cover_url": f"/api/admin/books/upload/{staging_id}/cover.jpg?v={int(datetime.now(timezone.utc).timestamp())}"}


def _get_staging_file(staging_id: str) -> str:
    """Resolve a staging_id to the absolute path of the staged book file. Used
    by the embedded-viewer endpoints so the admin can preview before commit."""
    rec = _STAGING.get(staging_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Staging not found")
    path = os.path.join(rec["dir"], rec["filename"])
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Staging file missing")
    return path


@app.get("/api/admin/books/upload/{staging_id}/file")
def admin_staging_file(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    """Serve the raw staged file for embedded preview (PDF, EPUB, image)."""
    return FileResponse(_get_staging_file(staging_id))


@app.get("/api/admin/books/upload/{staging_id}/fb2-content")
async def admin_staging_fb2_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    file_path = _get_staging_file(staging_id)
    lower = file_path.lower()
    if not (lower.endswith(".fb2") or lower.endswith(".fb2.zip")):
        raise HTTPException(status_code=400, detail="Not an FB2 file")
    try:
        xml_bytes = _read_fb2_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    return _convert_fb2(xml_bytes)


@app.get("/api/admin/books/upload/{staging_id}/md-content")
async def admin_staging_md_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    file_path = _get_staging_file(staging_id)
    text = _read_text_file(file_path)
    lower = file_path.lower()
    ext = os.path.splitext(lower)[1]
    if lower.endswith(".txt"):
        return _convert_txt(text)
    elif ext in CODE_EXTENSIONS:
        return _convert_code(text, ext[1:])
    return _convert_md(text)


@app.get("/api/admin/books/upload/{staging_id}/html-content")
async def admin_staging_html_content(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    file_path = _get_staging_file(staging_id)
    lower = file_path.lower()
    if not (lower.endswith(".html") or lower.endswith(".htm")
            or lower.endswith(".html.zip") or lower.endswith(".htm.zip")):
        raise HTTPException(status_code=400, detail="Not an HTML file")
    try:
        data = _read_html_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    return _convert_html(data)


@app.get("/api/admin/books/upload/{staging_id}/djvu-metadata")
async def admin_staging_djvu_metadata(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    file_path = _get_staging_file(staging_id)
    if not file_path.lower().endswith(".djvu"):
        raise HTTPException(status_code=400, detail="Not a DjVu file")
    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()
        return {"total_pages": len(doc.pages)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/books/upload/{staging_id}/djvu-outline")
async def admin_staging_djvu_outline(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    file_path = _get_staging_file(staging_id)
    if not file_path.lower().endswith(".djvu"):
        raise HTTPException(status_code=400, detail="Not a DjVu file")
    try:
        return {"toc": extract_djvu_outline(file_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/books/upload/{staging_id}/djvu-page")
async def admin_staging_djvu_page(
    staging_id: str,
    page: int,
    _admin: models.User = Depends(require_admin),
):
    file_path = _get_staging_file(staging_id)
    if not file_path.lower().endswith(".djvu"):
        raise HTTPException(status_code=400, detail="Not a DjVu file")
    if page < 1:
        raise HTTPException(status_code=400, detail="Invalid page number")
    headers = {"Cache-Control": "no-store"}
    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()
        if page > len(doc.pages):
            raise HTTPException(status_code=404, detail="Page not found")
        dpage = doc.pages[page - 1]
        job = dpage.decode(wait=True)
        width, height = job.width, job.height
        rect = (0, 0, width, height)
        fmt = djvu.decode.PixelFormatRgb()
        fmt.rows_top_to_bottom = True
        try:
            pixels = job.render(djvu.decode.RENDER_COLOR, rect, rect, fmt)
            img = Image.frombuffer('RGB', (width, height), pixels, 'raw', 'RGB', 0, 1)
        except djvu.decode.NotAvailable:
            img = Image.new('RGB', (width, height), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return Response(content=buf.getvalue(), media_type="image/jpeg", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/books/upload/{staging_id}")
async def admin_cancel_staging(
    staging_id: str,
    _admin: models.User = Depends(require_admin),
):
    rec = _STAGING.pop(staging_id, None)
    if rec:
        shutil.rmtree(rec.get("dir", ""), ignore_errors=True)
    return {"cancelled": staging_id}


@app.post("/api/admin/books/commit", response_model=schemas.AdminBookDetail)
async def admin_commit_book(
    payload: schemas.UploadCommitRequest,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Finalise a staged upload: move file into the CAS vault, link from
    /<top>/<sub>/<filename>, register Book + BookLocation, return AdminBookDetail."""
    _purge_expired_staging()
    rec = _STAGING.get(payload.staging_id)
    if not rec:
        raise HTTPException(status_code=410, detail="Staging expired or unknown")

    # top_dir is allowed to be empty (root) so admins can create a brand-new
    # top-level category by setting top=/ and subpath=<NewCategory>.
    raw_top = (payload.top_dir or "").strip().strip("/")
    top_dir = _safe_path_segment(raw_top) if raw_top else ""
    subpath = _safe_subpath(payload.subpath)
    if payload.clearance < 0 or payload.clearance > 100:
        raise HTTPException(status_code=400, detail="Clearance must be between 0 and 100")

    file_hash = rec["hash"]
    staging_filename = rec["filename"]
    requested_name = (payload.filename or staging_filename).strip()
    filename = _safe_path_segment(requested_name)
    # Force the rename to preserve the upload's effective extension so the
    # viewer's extension switch keeps working and stored content matches the
    # suffix. .fb2.zip is treated as a single suffix because we re-zip on upload.
    expected_suffix = (".fb2.zip" if staging_filename.lower().endswith(".fb2.zip")
                       else os.path.splitext(staging_filename)[1].lower())
    if expected_suffix and not filename.lower().endswith(expected_suffix):
        raise HTTPException(status_code=400, detail=f"Filename must keep extension {expected_suffix}")
    staging_book = os.path.join(rec["dir"], staging_filename)
    staging_cover = os.path.join(rec["dir"], "cover.jpg")

    # Destination paths — drop empty segments so "//" doesn't sneak in when
    # uploading directly under root.
    rel_parts = [seg for seg in (top_dir, subpath) if seg]
    rel_dir = "/".join(rel_parts)
    rel_path = f"{rel_dir}/{filename}" if rel_dir else filename
    abs_target_dir = os.path.abspath(os.path.join(BOOKS_DIR, rel_dir))
    abs_target = os.path.abspath(os.path.join(BOOKS_DIR, rel_path))
    if not abs_target.startswith(os.path.abspath(BOOKS_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Path escapes library root")

    # If a registered book *just* showed up at this hash via a concurrent
    # commit (unlikely, but cheap to check), bail out.
    if db.query(models.Book).filter(models.Book.id == file_hash).first():
        shutil.rmtree(rec["dir"], ignore_errors=True)
        _STAGING.pop(payload.staging_id, None)
        raise HTTPException(status_code=409, detail="Duplicate hash already registered")

    if os.path.lexists(abs_target):
        raise HTTPException(status_code=409, detail=f"A file already exists at {rel_path}")

    # 1. Move the staged file into the vault.
    vault_path = os.path.join(DATA_DIR, file_hash)
    try:
        if not os.path.exists(vault_path):
            os.replace(staging_book, vault_path)
        else:
            # Vault file already exists (e.g. from a deleted-but-not-purged earlier).
            os.remove(staging_book)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Vault write failed: {e}")

    # 2. Move the cover into place, if any.
    if os.path.exists(staging_cover):
        _ensure_writable_dir(COVERS_DIR)
        cover_dest = os.path.join(COVERS_DIR, f"{file_hash}.jpg")
        try:
            os.replace(staging_cover, cover_dest)
        except OSError as e:
            logging.warning("cover move failed: %s", e)

    # 3. Create the symlink (relative target so the CAS layout stays portable).
    try:
        os.makedirs(abs_target_dir, exist_ok=True)
        rel_vault_target = os.path.relpath(vault_path, abs_target_dir)
        os.symlink(rel_vault_target, abs_target)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Symlink failed: {e}")

    # 4. Register in DB.
    meta = payload.metadata.model_dump(exclude_unset=True)
    title = meta.get("title") or os.path.splitext(filename)[0]
    book = models.Book(
        id=file_hash,
        title=title,
        author=meta.get("author"),
        publisher=meta.get("publisher"),
        published=meta.get("published"),
        description=meta.get("description"),
        tags=meta.get("tags"),
        series=meta.get("series"),
        languages=meta.get("languages"),
        identifiers=meta.get("identifiers"),
        original_filename=filename,
        needs_review=bool(payload.needs_review),
        clearance=int(payload.clearance),
        import_date=_now_iso(),
    )
    db.add(book)
    db.add(models.BookLocation(hash_id=file_hash, symlink_path=rel_path))
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        # Best-effort filesystem rollback: remove the symlink we just created.
        try:
            if os.path.islink(abs_target):
                os.remove(abs_target)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"DB commit failed: {e}")
    db.refresh(book)

    # 5. Cleanup staging.
    shutil.rmtree(rec["dir"], ignore_errors=True)
    with _STAGING_LOCK:
        _STAGING.pop(payload.staging_id, None)

    return _book_to_admin_detail(book, db)


@app.post("/api/admin/integrity/verify/{hash_id}", response_model=schemas.IntegrityCheckResult)
async def admin_verify_book(
    hash_id: str,
    mode: str = "quick",
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if mode not in ("quick", "full"):
        raise HTTPException(status_code=400, detail="mode must be 'quick' or 'full'")
    return await asyncio.to_thread(_verify_book_sync, hash_id, mode, db)


# ---------- Bulk integrity jobs ----------

INTEGRITY_JOBS: dict[str, dict] = {}
INTEGRITY_JOBS_LOCK = threading.Lock()
INTEGRITY_JOB_TTL_SECONDS = 3600
INTEGRITY_ALL_RESULTS_CAP = 5000


def _sweep_expired_jobs() -> None:
    now = datetime.now(timezone.utc)
    with INTEGRITY_JOBS_LOCK:
        for jid in list(INTEGRITY_JOBS.keys()):
            job = INTEGRITY_JOBS[jid]
            fin = job.get("finished_at")
            if not fin or job["status"] == "running":
                continue
            try:
                finished = datetime.strptime(fin, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if (now - finished).total_seconds() > INTEGRITY_JOB_TTL_SECONDS:
                del INTEGRITY_JOBS[jid]


def _running_job_id() -> Optional[str]:
    with INTEGRITY_JOBS_LOCK:
        for jid, job in INTEGRITY_JOBS.items():
            if job["status"] == "running":
                return jid
    return None


def _job_summary(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "mode": job["mode"],
        "total": job["total"],
        "processed": job["processed"],
        "ok_count": job["ok_count"],
        "fail_count": job["fail_count"],
        "started_at": job["started_at"],
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
    }


async def _run_integrity_job(job_id: str) -> None:
    """Background worker. One book at a time, each in its own thread + session."""
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
    if job is None:
        return
    hash_ids = job["hash_ids"]
    mode = job["mode"]

    def _verify_one(hid: str) -> dict:
        session = SessionLocal()
        try:
            return _verify_book_sync(hid, mode, session)
        finally:
            session.close()

    try:
        for hid in hash_ids:
            if job.get("cancel_requested"):
                with INTEGRITY_JOBS_LOCK:
                    job["status"] = "cancelled"
                    job["finished_at"] = _now_iso()
                return
            try:
                result = await asyncio.to_thread(_verify_one, hid)
            except Exception as e:
                logging.exception("verify failed for %s", hid)
                result = {
                    "hash_id": hid, "mode": mode, "ok": False, "error": "exception",
                    "checks": [{"name": "worker", "ok": False, "detail": str(e)}],
                    "verified_at": _now_iso(),
                    "title": None, "original_filename": None, "db_update_failed": True,
                }
            with INTEGRITY_JOBS_LOCK:
                job["processed"] += 1
                if result["ok"]:
                    job["ok_count"] += 1
                else:
                    job["fail_count"] += 1
                    job["failures"].append(result)
                if len(job["all_results"]) < INTEGRITY_ALL_RESULTS_CAP:
                    job["all_results"].append(result)
                else:
                    job["all_results_truncated"] = True
        with INTEGRITY_JOBS_LOCK:
            if job["status"] == "running":
                job["status"] = "done"
                job["finished_at"] = _now_iso()
    except Exception as e:
        logging.exception("integrity job %s crashed", job_id)
        with INTEGRITY_JOBS_LOCK:
            job["status"] = "error"
            job["error"] = str(e)
            job["finished_at"] = _now_iso()


@app.post("/api/admin/integrity/jobs", response_model=schemas.IntegrityJobSummary)
async def admin_start_integrity_job(
    payload: schemas.IntegrityJobCreate,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _sweep_expired_jobs()
    if payload.scope not in ("all", "hash_ids"):
        raise HTTPException(status_code=400, detail="scope must be 'all' or 'hash_ids'")
    if payload.mode not in ("quick", "full"):
        raise HTTPException(status_code=400, detail="mode must be 'quick' or 'full'")

    if payload.scope == "all":
        hash_ids = [r[0] for r in db.query(models.Book.id).all()]
    else:
        hash_ids = list(dict.fromkeys(payload.hash_ids or []))  # de-dup, preserve order
        if not hash_ids:
            raise HTTPException(status_code=400, detail="hash_ids must be non-empty")

    running = _running_job_id()
    if running:
        raise HTTPException(
            status_code=409,
            detail={"reason": "job_running", "running_job_id": running},
        )

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "running",
        "mode": payload.mode,
        "total": len(hash_ids),
        "processed": 0,
        "ok_count": 0,
        "fail_count": 0,
        "started_at": _now_iso(),
        "finished_at": None,
        "cancel_requested": False,
        "error": None,
        "hash_ids": hash_ids,
        "failures": [],
        "all_results": [],
        "all_results_truncated": False,
    }
    with INTEGRITY_JOBS_LOCK:
        INTEGRITY_JOBS[job_id] = job

    asyncio.create_task(_run_integrity_job(job_id))
    return _job_summary(job)


@app.get("/api/admin/integrity/jobs")
async def admin_list_integrity_jobs(_admin: models.User = Depends(require_admin)):
    _sweep_expired_jobs()
    with INTEGRITY_JOBS_LOCK:
        jobs = [_job_summary(j) for j in INTEGRITY_JOBS.values()]
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return {"jobs": jobs}


@app.get("/api/admin/integrity/jobs/{job_id}", response_model=schemas.IntegrityJobDetail)
async def admin_get_integrity_job(
    job_id: str,
    include: str = "failures",
    _admin: models.User = Depends(require_admin),
):
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        detail = _job_summary(job)
        detail["failures"] = list(job["failures"])
        if include == "all":
            detail["all_results"] = list(job["all_results"])
        else:
            detail["all_results"] = None
        detail["all_results_truncated"] = bool(job.get("all_results_truncated"))
    return detail


@app.delete("/api/admin/integrity/jobs/{job_id}", response_model=schemas.IntegrityJobSummary)
async def admin_cancel_integrity_job(
    job_id: str,
    _admin: models.User = Depends(require_admin),
):
    with INTEGRITY_JOBS_LOCK:
        job = INTEGRITY_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job["status"] == "running":
            job["cancel_requested"] = True
        return _job_summary(job)


@app.get("/api/files/{path:path}")
async def get_file(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = os.path.join(BOOKS_DIR, path)
    if not os.path.abspath(file_path).startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    assert_can_read_path(file_path, current_user, db)
    return FileResponse(file_path)

def sanitize_fb2_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR, exists, and is an FB2 (or .fb2.zip) file."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    if not (lower.endswith(".fb2") or lower.endswith(".fb2.zip")):
        raise HTTPException(status_code=400, detail="Not an FB2 file")
    return target_path


def _read_fb2_bytes(file_path: str) -> bytes:
    if file_path.lower().endswith(".zip"):
        with zipfile.ZipFile(file_path) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".fb2"):
                    return zf.read(name)
        raise HTTPException(status_code=422, detail="No .fb2 entry inside zip")
    with open(file_path, "rb") as f:
        return f.read()


_FB2_NS = "{http://www.gribuser.ru/xml/fictionbook/2.0}"
_XLINK_NS = "{http://www.w3.org/1999/xlink}"


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


class _Fb2Renderer:
    SAFE_URL_RE = re.compile(r"^(https?:|mailto:|#|/)", re.IGNORECASE)

    def __init__(self, binaries: Dict[str, str], anchored: bool = True, collect_toc: bool = False):
        self.binaries = binaries
        self.anchored = anchored
        self.anchor = 0
        self.collect_toc = collect_toc
        self.toc: List[Dict[str, Any]] = []
        self._toc_parents: List[List[Dict[str, Any]]] = [self.toc]

    def _attr(self) -> str:
        if not self.anchored:
            return ""
        n = self.anchor
        self.anchor += 1
        return f' id="fb2-a-{n}" data-anchor="{n}"'

    def _last_anchor(self):
        return self.anchor - 1 if self.anchored and self.anchor > 0 else None

    _BLOCK_TAGS = {"p", "subtitle", "v", "empty-line", "title", "stanza", "epigraph"}

    @classmethod
    def _plain_text(cls, el) -> str:
        # itertext() concatenates text without separators, so adjacent block
        # children like <p>A</p><p>B</p> would render as "AB". Walk the tree
        # ourselves and insert a space after each block child while preserving
        # inline runs (so <p>Hi <em>world</em>!</p> stays "Hi world!").
        out: List[str] = []

        def walk(node):
            if node.text:
                out.append(node.text)
            for child in node:
                walk(child)
                if child.tail:
                    out.append(child.tail)
                if _strip_ns(child.tag) in cls._BLOCK_TAGS:
                    out.append(" ")

        walk(el)
        return " ".join("".join(out).split())

    def _href(self, el) -> str:
        return el.get(_XLINK_NS + "href") or el.get("href") or ""

    def _safe_href(self, href: str) -> str:
        return href if self.SAFE_URL_RE.match(href) else "#"

    def render_inline(self, el) -> str:
        out = []
        if el.text:
            out.append(_html_escape(el.text))
        for child in el:
            tag = _strip_ns(child.tag)
            inner = self.render_inline(child)
            if tag == "emphasis":
                out.append(f"<em>{inner}</em>")
            elif tag == "strong":
                out.append(f"<strong>{inner}</strong>")
            elif tag == "strikethrough":
                out.append(f"<s>{inner}</s>")
            elif tag == "sub":
                out.append(f"<sub>{inner}</sub>")
            elif tag == "sup":
                out.append(f"<sup>{inner}</sup>")
            elif tag == "code":
                out.append(f"<code>{inner}</code>")
            elif tag == "a":
                href = self._safe_href(self._href(child))
                note_type = child.get("type") or ""
                cls = "fb2-link fb2-note" if note_type == "note" else "fb2-link"
                out.append(f'<a href="{_html_escape(href)}" class="{cls}">{inner}</a>')
            elif tag == "image":
                bid = self._href(child).lstrip("#")
                src = self.binaries.get(bid)
                if src:
                    out.append(f'<img src="{src}" class="fb2-inline-img" alt="" />')
            elif tag == "style":
                out.append(inner)
            elif tag == "empty-line":
                out.append("<br />")
            else:
                out.append(inner)
            if child.tail:
                out.append(_html_escape(child.tail))
        return "".join(out)

    def _title_text(self, el) -> str:
        # <title> usually contains <p> children
        parts = []
        for c in el:
            if _strip_ns(c.tag) == "p":
                parts.append(self.render_inline(c))
            elif _strip_ns(c.tag) == "empty-line":
                parts.append("<br />")
        if not parts:
            parts.append(self.render_inline(el))
        return " ".join(parts)

    def render_block(self, el, depth: int = 0) -> str:
        tag = _strip_ns(el.tag)

        if tag == "section":
            entry = None
            if self.collect_toc:
                entry = {"title": "", "anchor": None, "children": []}
                self._toc_parents[-1].append(entry)
                self._toc_parents.append(entry["children"])
            try:
                parts = []
                for c in el:
                    ctag = _strip_ns(c.tag)
                    if ctag == "title":
                        level = min(max(depth + 1, 2), 6)
                        attr = self._attr()
                        if entry is not None:
                            entry["anchor"] = self._last_anchor()
                            entry["title"] = self._plain_text(c)
                        parts.append(
                            f'<h{level}{attr} class="fb2-section-title">'
                            f'{self._title_text(c)}</h{level}>'
                        )
                    elif ctag == "section":
                        parts.append(self.render_block(c, depth + 1))
                    else:
                        parts.append(self.render_block(c, depth))
                return f'<section class="fb2-section">{"".join(parts)}</section>'
            finally:
                if self.collect_toc:
                    self._toc_parents.pop()

        attr = self._attr()

        if tag == "p":
            return f'<p{attr} class="fb2-p">{self.render_inline(el)}</p>'
        if tag == "subtitle":
            return f'<h4{attr} class="fb2-subtitle">{self._title_text(el)}</h4>'
        if tag == "empty-line":
            return f'<div{attr} class="fb2-empty-line"></div>'
        if tag == "image":
            bid = self._href(el).lstrip("#")
            src = self.binaries.get(bid)
            if not src:
                return ""
            return f'<div{attr} class="fb2-image-wrap"><img src="{src}" class="fb2-image" alt="" /></div>'
        if tag in ("epigraph", "cite"):
            inner_parts = []
            for c in el:
                if _strip_ns(c.tag) in ("p", "poem", "subtitle", "empty-line", "text-author"):
                    inner_parts.append(self.render_block(c, depth))
            return f'<blockquote{attr} class="fb2-{tag}">{"".join(inner_parts)}</blockquote>'
        if tag == "text-author":
            return f'<p{attr} class="fb2-text-author">{self.render_inline(el)}</p>'
        if tag == "poem":
            inner = []
            for c in el:
                ctag = _strip_ns(c.tag)
                if ctag == "title":
                    inner.append(f'<div class="fb2-poem-title">{self._title_text(c)}</div>')
                elif ctag == "stanza":
                    lines = []
                    for v in c:
                        vtag = _strip_ns(v.tag)
                        if vtag == "v":
                            lines.append(f'<div class="fb2-v">{self.render_inline(v)}</div>')
                        elif vtag == "title":
                            lines.append(f'<div class="fb2-stanza-title">{self._title_text(v)}</div>')
                    inner.append(f'<div class="fb2-stanza">{"".join(lines)}</div>')
                elif ctag == "text-author":
                    inner.append(f'<div class="fb2-text-author">{self.render_inline(c)}</div>')
                elif ctag == "epigraph":
                    inner.append(self.render_block(c, depth))
            return f'<div{attr} class="fb2-poem">{"".join(inner)}</div>'
        # Unknown block: render its inline contents in a div so text isn't lost
        return f'<div{attr} class="fb2-other">{self.render_inline(el)}</div>'


def _extract_binaries(root) -> Dict[str, str]:
    """Build {binary-id: data:URI} for all <binary> elements."""
    out: Dict[str, str] = {}
    for b in root.iter(_FB2_NS + "binary"):
        bid = b.get("id")
        ctype = b.get("content-type") or "application/octet-stream"
        if not bid or not b.text:
            continue
        # Re-encode to drop whitespace inside the base64 blob
        try:
            raw = base64.b64decode(b.text)
            out[bid] = f"data:{ctype};base64,{base64.b64encode(raw).decode('ascii')}"
        except Exception:
            continue
    return out


def _extract_metadata(root) -> Dict[str, Any]:
    desc = root.find(_FB2_NS + "description")
    title = ""
    authors: List[str] = []
    if desc is not None:
        ti = desc.find(_FB2_NS + "title-info")
        if ti is not None:
            book_title = ti.find(_FB2_NS + "book-title")
            if book_title is not None and book_title.text:
                title = book_title.text.strip()
            for a in ti.findall(_FB2_NS + "author"):
                first = a.findtext(_FB2_NS + "first-name", default="").strip()
                middle = a.findtext(_FB2_NS + "middle-name", default="").strip()
                last = a.findtext(_FB2_NS + "last-name", default="").strip()
                nick = a.findtext(_FB2_NS + "nickname", default="").strip()
                full = " ".join(p for p in (first, middle, last) if p) or nick
                if full:
                    authors.append(full)
    return {"title": title, "authors": authors}


def _render_note_section(section, binaries: Dict[str, str]) -> str:
    """Render the contents of a single <section> from a notes body, dropping
    its <title> (which is usually just the note number, redundant with the
    inline marker the user clicked)."""
    note_renderer = _Fb2Renderer(binaries, anchored=False)
    parts = []
    for c in section:
        if _strip_ns(c.tag) == "title":
            continue
        parts.append(note_renderer.render_block(c))
    return "".join(parts)


def _convert_fb2(xml_bytes: bytes) -> Dict[str, Any]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise HTTPException(status_code=422, detail=f"Invalid FB2 XML: {e}")
    binaries = _extract_binaries(root)
    renderer = _Fb2Renderer(binaries, collect_toc=True)
    bodies_html: List[str] = []
    notes: Dict[str, str] = {}
    for body in root.findall(_FB2_NS + "body"):
        raw_name = body.get("name") or ""
        if raw_name:
            # Footnotes / comments / etc. — collect by section id for tooltip
            # rendering on the frontend, do not append to the main body HTML.
            for section in body.iter(_FB2_NS + "section"):
                sid = section.get("id")
                if sid:
                    notes[sid] = _render_note_section(section, binaries)
            continue
        parts = []
        # body may have its own <title> and <epigraph> before sections
        for c in body:
            ctag = _strip_ns(c.tag)
            if ctag == "title":
                attr = renderer._attr()
                if renderer.collect_toc:
                    renderer.toc.append({
                        "title": renderer._plain_text(c),
                        "anchor": renderer._last_anchor(),
                        "children": [],
                    })
                parts.append(
                    f'<h2{attr} class="fb2-body-title">'
                    f'{renderer._title_text(c)}</h2>'
                )
            else:
                parts.append(renderer.render_block(c))
        bodies_html.append(f'<div class="fb2-body">{"".join(parts)}</div>')
    meta = _extract_metadata(root)
    return {
        "title": meta["title"],
        "authors": meta["authors"],
        "html": "".join(bodies_html),
        "anchor_count": renderer.anchor,
        "notes": notes,
        "toc": renderer.toc,
    }


def _extract_annotation_html(root) -> str:
    desc = root.find(_FB2_NS + "description")
    if desc is None:
        return ""
    ti = desc.find(_FB2_NS + "title-info")
    if ti is None:
        return ""
    annotation = ti.find(_FB2_NS + "annotation")
    if annotation is None:
        return ""
    renderer = _Fb2Renderer({}, anchored=False)
    return "".join(renderer.render_block(c) for c in annotation)


@app.get("/api/fb2-content")
async def fb2_content(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_fb2_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        xml_bytes = _read_fb2_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    return _convert_fb2(xml_bytes)


@app.get("/api/fb2-metadata")
async def fb2_metadata(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_fb2_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        xml_bytes = _read_fb2_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise HTTPException(status_code=422, detail=f"Invalid FB2 XML: {e}")
    meta = _extract_metadata(root)
    return {
        "title": meta["title"],
        "authors": meta["authors"],
        "annotation_html": _extract_annotation_html(root),
    }


# ---------------- Markdown / plain-text viewer ----------------

CODE_EXTENSIONS = {
    ".py", ".c", ".cpp", ".h", ".hpp", ".js", ".ts", ".jsx", ".tsx",
    ".lua", ".sh", ".bash", ".rs", ".go", ".java", ".css", ".scss",
    ".json", ".xml", ".yaml", ".yml", ".sql", ".ini"
}

def sanitize_text_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR, exists, and is a .md/.markdown/.txt or code file."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    ext = os.path.splitext(lower)[1]
    if not (lower.endswith(".md") or lower.endswith(".markdown") or lower.endswith(".txt") or ext in CODE_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Not a Markdown, text, or supported code file")
    return target_path


def _read_text_file(file_path: str) -> str:
    with open(file_path, "rb") as f:
        raw = f.read()
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_MD_SAFE_URL_RE = re.compile(r"^(https?:|mailto:|/|#|\.\.?/)", re.IGNORECASE)


class _MdRenderer:
    """Minimal CommonMark-ish renderer. Handles ATX/setext headings, paragraphs,
    fenced code, blockquotes, flat lists, horizontal rules, and inline emphasis/
    code/links/images. Raw HTML in source is escaped, never passed through."""

    _ATX_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
    _FENCE_RE = re.compile(r"^\s{0,3}(```+|~~~+)\s*([^\s`]*)\s*$")
    _HR_RE = re.compile(r"^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})\s*$")
    _BQ_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
    _UL_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
    _OL_RE = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
    _SETEXT_H1_RE = re.compile(r"^=+\s*$")
    _SETEXT_H2_RE = re.compile(r"^-+\s*$")

    def __init__(self, collect_toc: bool = True):
        self.collect_toc = collect_toc
        self.anchor = 0
        self.toc_flat: List[Dict[str, Any]] = []

    def _attr(self):
        n = self.anchor
        self.anchor += 1
        return f' id="md-a-{n}" data-anchor="{n}"', n

    @staticmethod
    def _strip_html(html: str) -> str:
        return re.sub(r"<[^>]+>", "", html)

    @classmethod
    def _safe_url(cls, url: str) -> str:
        url = url.strip()
        return url if _MD_SAFE_URL_RE.match(url) else "#"

    def _inline(self, text: str) -> str:
        placeholders: List[str] = []

        def stash(html: str) -> str:
            placeholders.append(html)
            return f"\x00{len(placeholders) - 1}\x00"

        # Inline code first (its contents must NOT have other rules applied).
        def code_repl(m):
            return stash(f"<code>{_html_escape(m.group(2))}</code>")
        text = re.sub(r"(`+)([^`\n]+?)\1", code_repl, text)

        # Images
        def image_repl(m):
            alt, url = m.group(1), m.group(2)
            return stash(
                f'<img src="{_html_escape(self._safe_url(url))}" '
                f'alt="{_html_escape(alt)}" class="md-image" />'
            )
        text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", image_repl, text)

        # Links
        def link_repl(m):
            label, url = m.group(1), m.group(2)
            return stash(
                f'<a href="{_html_escape(self._safe_url(url))}" class="md-link">'
                f'{self._inline(label)}</a>'
            )
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", link_repl, text)

        # Autolinks <http://...> / <mailto:...>
        def auto_repl(m):
            url = m.group(1)
            return stash(
                f'<a href="{_html_escape(url)}" class="md-link">{_html_escape(url)}</a>'
            )
        text = re.sub(r"<((?:https?:|mailto:)[^>\s]+)>", auto_repl, text)

        text = _html_escape(text)

        # Hard line breaks (two+ trailing spaces before \n) — convert before
        # collapsing newlines elsewhere.
        text = re.sub(r" {2,}\n", "<br />\n", text)

        # Bold, then italic. Order matters so ** doesn't get eaten as two * runs.
        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"(?<![\*\w])\*(?=\S)([^\*\n]+?)(?<=\S)\*(?![\*\w])", r"<em>\1</em>", text)
        text = re.sub(r"(?<![_\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![_\w])", r"<em>\1</em>", text)
        text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", text, flags=re.S)

        return re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)

    def _emit_heading(self, level: int, raw: str) -> str:
        attr, n = self._attr()
        inner = self._inline(raw.strip())
        if self.collect_toc:
            self.toc_flat.append({
                "title": self._strip_html(inner),
                "level": level,
                "anchor": n,
            })
        return f'<h{level}{attr} class="md-h{level}">{inner}</h{level}>'

    def _emit_paragraph(self, lines: List[str]) -> str:
        attr, _ = self._attr()
        joined = "\n".join(lines)
        content = self._inline(joined)
        # Soft line breaks → single space (CommonMark default). Skip newlines
        # that were already promoted to <br />.
        content = re.sub(r"(?<!<br />)\n", " ", content)
        return f'<p{attr} class="md-p">{content}</p>'

    def _emit_codeblock(self, code: str, lang: str = "") -> str:
        attr, _ = self._attr()
        lang_class = f' class="language-{_html_escape(lang)}"' if lang else ""
        return (
            f'<pre{attr} class="md-codeblock"><code{lang_class}>'
            f'{_html_escape(code)}</code></pre>'
        )

    def _emit_blockquote(self, lines: List[str]) -> str:
        attr, _ = self._attr()
        inner = "<br />".join(self._inline(l) for l in lines)
        return f'<blockquote{attr} class="md-blockquote">{inner}</blockquote>'

    def _emit_list(self, ordered: bool, items: List[List[str]]) -> str:
        attr, _ = self._attr()
        tag = "ol" if ordered else "ul"
        parts = []
        for item_lines in items:
            content = self._inline(" ".join(l.strip() for l in item_lines))
            parts.append(f'<li class="md-li">{content}</li>')
        return f'<{tag}{attr} class="md-{tag}">{"".join(parts)}</{tag}>'

    def _emit_hr(self) -> str:
        attr, _ = self._attr()
        return f'<hr{attr} class="md-hr" />'

    def _is_block_start(self, line: str, nxt: str) -> bool:
        if self._ATX_RE.match(line): return True
        if self._FENCE_RE.match(line): return True
        if self._HR_RE.match(line): return True
        if self._BQ_RE.match(line): return True
        if self._UL_RE.match(line): return True
        if self._OL_RE.match(line): return True
        if nxt and (self._SETEXT_H1_RE.match(nxt) or self._SETEXT_H2_RE.match(nxt)):
            return True
        return False

    def render(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        lines = text.split("\n")
        n = len(lines)
        out: List[str] = []
        i = 0
        while i < n:
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            m = self._FENCE_RE.match(line)
            if m:
                fence, lang = m.group(1), m.group(2)
                i += 1
                code_lines: List[str] = []
                while i < n and not lines[i].strip().startswith(fence):
                    code_lines.append(lines[i])
                    i += 1
                if i < n:
                    i += 1  # consume closing fence
                out.append(self._emit_codeblock("\n".join(code_lines), lang))
                continue

            m = self._ATX_RE.match(line)
            if m:
                out.append(self._emit_heading(len(m.group(1)), m.group(2)))
                i += 1
                continue

            if i + 1 < n:
                nxt = lines[i + 1]
                if self._SETEXT_H1_RE.match(nxt) and len(nxt.strip()) >= 1:
                    out.append(self._emit_heading(1, stripped))
                    i += 2
                    continue
                if self._SETEXT_H2_RE.match(nxt) and len(nxt.strip()) >= 2:
                    out.append(self._emit_heading(2, stripped))
                    i += 2
                    continue

            if self._HR_RE.match(line):
                out.append(self._emit_hr())
                i += 1
                continue

            if self._BQ_RE.match(line):
                bq: List[str] = []
                while i < n and self._BQ_RE.match(lines[i]):
                    bq.append(self._BQ_RE.match(lines[i]).group(1))
                    i += 1
                out.append(self._emit_blockquote(bq))
                continue

            m_ul = self._UL_RE.match(line)
            m_ol = self._OL_RE.match(line)
            if m_ul or m_ol:
                ordered = m_ol is not None
                items: List[List[str]] = []
                while i < n:
                    cur = lines[i]
                    if not cur.strip():
                        break
                    mm = self._OL_RE.match(cur) if ordered else self._UL_RE.match(cur)
                    if mm:
                        items.append([mm.group(3)])
                        i += 1
                        continue
                    # Continuation: indented line under last item
                    if items and cur.startswith(" "):
                        items[-1].append(cur)
                        i += 1
                        continue
                    break
                out.append(self._emit_list(ordered, items))
                continue

            # Paragraph
            para = [line]
            i += 1
            while i < n and lines[i].strip():
                nxt = lines[i + 1] if i + 1 < n else ""
                if self._is_block_start(lines[i], nxt):
                    break
                para.append(lines[i])
                i += 1
            out.append(self._emit_paragraph(para))

        return "".join(out)


def _nest_toc(flat: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert flat [{title, level, anchor}] into a nested tree of
    {title, anchor, children}."""
    root: List[Dict[str, Any]] = []
    stack: List[tuple] = []  # (level, children_list)
    for item in flat:
        entry = {"title": item["title"], "anchor": item["anchor"], "children": []}
        while stack and stack[-1][0] >= item["level"]:
            stack.pop()
        (stack[-1][1] if stack else root).append(entry)
        stack.append((item["level"], entry["children"]))
    return root


def _extract_md_title(toc_flat: List[Dict[str, Any]]) -> str:
    for item in toc_flat:
        if item["level"] == 1 and item["title"].strip():
            return item["title"].strip()
    return ""


def _convert_md(text: str) -> Dict[str, Any]:
    renderer = _MdRenderer(collect_toc=True)
    html = renderer.render(text)
    return {
        "title": _extract_md_title(renderer.toc_flat),
        "html": html,
        "raw": text,
        "toc": _nest_toc(renderer.toc_flat),
        "anchor_count": renderer.anchor,
    }


def _convert_code(text: str, lang: str) -> Dict[str, Any]:
    """Source-code viewer: emit a single <pre class="md-codeblock"><code> with
    the file contents HTML-escaped. Bypasses the markdown parser so that lines
    starting with ``` (e.g. embedded in docstrings) don't terminate the block."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lang_class = f' class="language-{_html_escape(lang)}"' if lang else ""
    html = (
        f'<pre id="md-a-0" data-anchor="0" class="md-codeblock">'
        f'<code{lang_class}>{_html_escape(normalized)}</code></pre>'
    )
    return {"title": "", "html": html, "raw": normalized, "toc": [], "anchor_count": 1}


def _render_code_snippet_html(text: str, lang: str) -> str:
    lang_class = f' class="language-{_html_escape(lang)}"' if lang else ""
    return (
        f'<pre class="md-codeblock"><code{lang_class}>'
        f'{_html_escape(text)}</code></pre>'
    )


def _convert_txt(text: str) -> Dict[str, Any]:
    """Plain-text viewer: each blank-line-separated block becomes one anchored
    <pre>, preserving the author's line wrapping and any ASCII layout."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    parts: List[str] = []
    anchor = 0
    for block in re.split(r"\n\s*\n", normalized):
        if not block.strip():
            continue
        escaped = _html_escape(block.rstrip("\n"))
        parts.append(
            f'<pre id="md-a-{anchor}" data-anchor="{anchor}" class="md-txt-block">{escaped}</pre>'
        )
        anchor += 1
    return {"title": "", "html": "".join(parts), "raw": normalized, "toc": [], "anchor_count": anchor}


@app.get("/api/md-content")
async def md_content(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_text_path(path)
    assert_can_read_path(file_path, current_user, db)
    text = _read_text_file(file_path)
    lower = file_path.lower()
    ext = os.path.splitext(lower)[1]
    if lower.endswith(".txt"):
        return _convert_txt(text)
    elif ext in CODE_EXTENSIONS:
        return _convert_code(text, ext[1:])
    return _convert_md(text)


@app.get("/api/text-preview")
async def text_preview(
    path: str,
    max_chars: int = 2000,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Return up to max_chars of text from a .md/.txt file for use as a
    cover-slot placeholder preview. For .md the snippet is also rendered to
    HTML so the preview can mirror the in-viewer formatting. Clamp the limit
    to keep responses tiny."""
    file_path = sanitize_text_path(path)
    assert_can_read_path(file_path, current_user, db)
    text = _read_text_file(file_path)
    limit = max(200, min(int(max_chars), 8000))
    snippet = text[:limit]
    html = ""
    lower = file_path.lower()
    ext = os.path.splitext(lower)[1]
    if not lower.endswith(".txt"):
        if ext in CODE_EXTENSIONS:
            html = _render_code_snippet_html(snippet, ext[1:])
        else:
            html = _MdRenderer(collect_toc=False).render(snippet)
    return {
        "text": snippet,
        "html": html,
        "truncated": len(text) > len(snippet),
    }


# ---------------- HTML viewer ----------------

def sanitize_html_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR, exists, and is an .html/.htm/.html.zip file."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    if not (lower.endswith(".html") or lower.endswith(".htm")
            or lower.endswith(".html.zip") or lower.endswith(".htm.zip")):
        raise HTTPException(status_code=400, detail="Not an HTML file")
    return target_path


def _read_html_bytes(file_path: str) -> bytes:
    if file_path.lower().endswith(".zip"):
        with zipfile.ZipFile(file_path) as zf:
            html_entries = [n for n in zf.namelist()
                            if n.lower().endswith((".html", ".htm"))
                            and not n.endswith("/")]
            if not html_entries:
                raise HTTPException(status_code=422, detail="No .html entry inside zip")
            # Prefer the shortest path (typically the document root, not assets/x.html).
            html_entries.sort(key=len)
            return zf.read(html_entries[0])
    with open(file_path, "rb") as f:
        return f.read()


_HTML_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset\s*=\s*["\']?\s*([A-Za-z0-9_\-]+)', re.IGNORECASE
)


def _decode_html_bytes(data: bytes) -> str:
    """Decode HTML bytes using a charset declared via <meta charset>, falling
    back to utf-8 then latin-1. Only the first ~2KB is scanned for the meta
    tag, matching how browsers sniff."""
    m = _HTML_CHARSET_RE.search(data[:2048])
    if m:
        try:
            return data.decode(m.group(1).decode("ascii", errors="replace"))
        except (LookupError, UnicodeDecodeError):
            pass
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_HTML_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

_HTML_DROP_TAGS = {
    "script", "style", "iframe", "object", "embed", "form", "input", "button",
    "select", "textarea", "link", "meta", "svg", "math", "frame", "frameset",
    "applet", "noscript",
}

_HTML_UNWRAP_TAGS = {"html", "body"}

_HTML_ALLOWED_TAGS = {
    "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo",
    "blockquote", "br", "caption", "cite", "code", "col", "colgroup",
    "dd", "del", "details", "dfn", "div", "dl", "dt", "em", "figcaption",
    "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "i", "img", "ins", "kbd", "li", "main", "mark", "nav", "ol",
    "p", "pre", "q", "rp", "rt", "ruby", "s", "samp", "section", "small",
    "span", "strong", "sub", "summary", "sup", "table", "tbody", "td",
    "tfoot", "th", "thead", "time", "tr", "u", "ul", "var", "wbr",
}

_HTML_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"start", "reversed", "type"},
    "li": {"value"},
    "time": {"datetime"},
    "q": {"cite"},
    "blockquote": {"cite"},
}

_HTML_ANCHORED_TAGS = {"p", "div", "section", "article", "pre", "blockquote",
                       "h1", "h2", "h3", "h4", "h5", "h6"}
_HTML_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

_HTML_SAFE_HREF_RE = re.compile(
    r"^(https?:|mailto:|tel:|#|/|\.\.?/)", re.IGNORECASE
)
_HTML_SAFE_IMG_RE = re.compile(
    r"^(https?:|data:image/|/|\.\.?/)", re.IGNORECASE
)


import html.parser as _hp  # avoid clashing with the html.escape import at top


class _HtmlSanitizer(_hp.HTMLParser):
    """Streaming HTML sanitizer that drops scripts/styles/forms, strips event
    handlers and dangerous URLs, anchors block elements for progress tracking,
    and collects a flat TOC from h1–h6."""

    def __init__(self, collect_toc: bool = True, max_chars: int | None = None):
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.collect_toc = collect_toc
        self.toc_flat: List[Dict[str, Any]] = []
        self.title: str = ""
        self.anchor: int = 0
        self._suppress_depth = 0
        self._head_depth = 0
        self._title_depth = 0
        self._heading_stack: List[tuple] = []  # (level, anchor, [text parts])
        self._max_chars = max_chars
        self.truncated = False
        self._len = 0

    def _emit(self, s: str) -> None:
        if self._max_chars is not None and self._len >= self._max_chars:
            self.truncated = True
            return
        self.out.append(s)
        self._len += len(s)

    def _format_attrs(self, tag: str, attrs):
        allowed = _HTML_ALLOWED_ATTRS.get(tag, set())
        parts = []
        for k, v in attrs:
            k = (k or "").lower()
            if not k or k.startswith("on") or k not in allowed:
                continue
            if v is None:
                parts.append(f" {k}")
                continue
            v = v.strip()
            if tag == "a" and k == "href":
                if not _HTML_SAFE_HREF_RE.match(v):
                    v = "#"
            elif tag == "img" and k == "src":
                if not _HTML_SAFE_IMG_RE.match(v):
                    continue
            parts.append(f' {k}="{_html_escape(v, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "head":
            self._head_depth += 1
            self._suppress_depth += 1
            return
        if self._head_depth > 0:
            if tag == "title":
                self._title_depth += 1
            return
        if tag in _HTML_DROP_TAGS:
            self._suppress_depth += 1
            return
        if self._suppress_depth:
            return
        if tag in _HTML_UNWRAP_TAGS or tag not in _HTML_ALLOWED_TAGS:
            return
        attr_str = self._format_attrs(tag, attrs)
        if tag in _HTML_ANCHORED_TAGS:
            n = self.anchor
            self.anchor += 1
            attr_str = f' id="md-a-{n}" data-anchor="{n}"' + attr_str
            if tag in _HTML_HEADING_TAGS:
                self._heading_stack.append((int(tag[1]), n, []))
        slash = "/" if tag in _HTML_VOID_TAGS else ""
        self._emit(f"<{tag}{attr_str}{slash}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "head":
            self._head_depth = max(0, self._head_depth - 1)
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._head_depth > 0:
            if tag == "title":
                self._title_depth = max(0, self._title_depth - 1)
            return
        if tag in _HTML_DROP_TAGS:
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._suppress_depth:
            return
        if tag in _HTML_UNWRAP_TAGS or tag not in _HTML_ALLOWED_TAGS:
            return
        if tag in _HTML_HEADING_TAGS and self._heading_stack:
            level, n, buf = self._heading_stack.pop()
            text = "".join(buf).strip()
            if self.collect_toc and text:
                self.toc_flat.append({"title": text, "level": level, "anchor": n})
        if tag in _HTML_VOID_TAGS:
            return
        self._emit(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        # XHTML-style self-closing. Treat as a start tag; void tags already
        # render as self-closing, non-void inputs were authored as a single
        # element so we still don't want a separate end emit.
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if self._title_depth > 0 and not self.title:
            t = data.strip()
            if t:
                self.title = t
            return
        if self._head_depth > 0 or self._suppress_depth:
            return
        if self._heading_stack:
            self._heading_stack[-1][2].append(data)
        self._emit(_html_escape(data, quote=False))


def _convert_html(html_bytes: bytes, max_chars: int | None = None) -> Dict[str, Any]:
    raw = _decode_html_bytes(html_bytes)
    sanitizer = _HtmlSanitizer(collect_toc=True, max_chars=max_chars)
    sanitizer.feed(raw)
    sanitizer.close()
    title = sanitizer.title
    if not title:
        for item in sanitizer.toc_flat:
            if item["level"] == 1 and item["title"].strip():
                title = item["title"].strip()
                break
    return {
        "title": title,
        "html": "".join(sanitizer.out),
        "raw": raw,
        "toc": _nest_toc(sanitizer.toc_flat),
        "anchor_count": sanitizer.anchor,
        "truncated": sanitizer.truncated,
    }


@app.get("/api/html-content")
async def html_content(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_html_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        data = _read_html_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    return _convert_html(data)


@app.get("/api/html-preview")
async def html_preview(
    path: str,
    max_chars: int = 2000,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Sanitized HTML snippet for the ItemView cover-slot placeholder. The
    sanitizer stops appending after max_chars of output, so the response stays
    small even for large books."""
    file_path = sanitize_html_path(path)
    assert_can_read_path(file_path, current_user, db)
    limit = max(200, min(int(max_chars), 8000))
    try:
        data = _read_html_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    result = _convert_html(data, max_chars=limit)
    return {
        "title": result["title"],
        "html": result["html"],
        "truncated": result["truncated"],
    }


def sanitize_djvu_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR and exists."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    if not target_path.lower().endswith(".djvu"):
        raise HTTPException(status_code=400, detail="Not a DjVu file")
    return target_path


def _parse_djvu_bookmark(node) -> dict:
    """Convert one djvu sexpr bookmark node into a TOC entry.

    A node is a native tuple (title, link, *children); `link` is "#<page>"
    where a numeric page is 1-based (matching doc.pages[page-1])."""
    title = str(node[0]) if len(node) > 0 else ""
    link = node[1] if len(node) > 1 else ""
    page = None
    if isinstance(link, str) and link.startswith("#") and link[1:].isdigit():
        page = int(link[1:])
    children = [_parse_djvu_bookmark(c) for c in node[2:]]
    return {"title": title, "page": page, "children": children}


def extract_djvu_outline(file_path: str) -> list:
    """Extract the embedded outline (bookmarks) of a DjVu file as a nested
    list of {title, page, children}. Returns [] when the file has none."""
    ctx = djvu.decode.Context()
    doc = ctx.new_document(djvu.decode.FileURI(file_path))
    doc.decoding_job.wait()
    outline = doc.outline
    outline.wait()
    items = list(outline.sexpr)  # [] when the file has no outline
    # items[0] is Symbol('bookmarks'); the rest are bookmark entries.
    return [_parse_djvu_bookmark(e.value) for e in items[1:]]


@app.get("/api/djvu-metadata")
async def djvu_metadata(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_djvu_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()
        total_pages = len(doc.pages)
        return {"total_pages": total_pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/djvu-outline")
async def djvu_outline(
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_djvu_path(path)
    assert_can_read_path(file_path, current_user, db)
    try:
        return {"toc": extract_djvu_outline(file_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/djvu-page")
async def djvu_page(
    path: str,
    page: int,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_djvu_path(path)
    assert_can_read_path(file_path, current_user, db)
    if page < 1:
        raise HTTPException(status_code=400, detail="Invalid page number")

    headers = {"Cache-Control": "public, max-age=86400"}

    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()

        if page > len(doc.pages):
            raise HTTPException(status_code=404, detail="Page not found")

        djvu_page = doc.pages[page - 1]
        job = djvu_page.decode(wait=True)

        width, height = job.width, job.height
        rect = (0, 0, width, height)
        format = djvu.decode.PixelFormatRgb()
        format.rows_top_to_bottom = True

        try:
            pixels = job.render(djvu.decode.RENDER_COLOR, rect, rect, format)
            img = Image.frombuffer('RGB', (width, height), pixels, 'raw', 'RGB', 0, 1)
        except djvu.decode.NotAvailable:
            img = Image.new('RGB', (width, height), (255, 255, 255))

        output_buffer = io.BytesIO()
        img.save(output_buffer, format="JPEG", quality=85)

        return Response(content=output_buffer.getvalue(), media_type="image/jpeg", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/covers/{hash_id}")
async def get_cover(
    hash_id: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    cover_path = os.path.join(BOOKS_DIR, ".data", "covers", f"{hash_id}.jpg")
    if not os.path.exists(cover_path) or not os.path.isfile(cover_path):
        raise HTTPException(status_code=404, detail="Cover not found")
    if not _is_admin(current_user):
        required = _book_clearance(hash_id, db)
        if required > _clearance_of(current_user):
            raise HTTPException(status_code=403, detail="Forbidden")
    return FileResponse(cover_path)

@app.get("/api/favorites")
async def get_favorites(current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if current_user is None:  # guest — no per-user state
        return {"items": []}
    q = db.query(models.Favorite, models.Book, models.BookLocation).join(
        models.Book, models.Favorite.hash_id == models.Book.id
    ).outerjoin(
        models.BookLocation, models.Book.id == models.BookLocation.hash_id
    ).filter(models.Favorite.user_id == current_user.id)
    if not current_user.is_admin:
        q = q.filter(models.Book.clearance <= (current_user.clearance or 0))
    results = q.all()

    fav_dict = {}
    for fav, book, loc in results:
        if fav.id not in fav_dict:
            fav_dict[fav.id] = {
                "favorite_id": fav.id,
                "hash_id": fav.hash_id,
                "title": book.title,
                "author": book.author,
                "description": book.description,
                "original_filename": book.original_filename,
                "path": loc.symlink_path if loc else None
            }

    return {"items": list(fav_dict.values())}

@app.post("/api/favorites", response_model=schemas.FavoriteResponse)
async def add_favorite(fav: schemas.FavoriteCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin and _book_clearance(fav.hash_id, db) > (current_user.clearance or 0):
        raise HTTPException(status_code=403, detail="Forbidden")
    existing = db.query(models.Favorite).filter(
        models.Favorite.user_id == current_user.id,
        models.Favorite.hash_id == fav.hash_id
    ).first()
    if existing:
        return existing

    new_fav = models.Favorite(user_id=current_user.id, hash_id=fav.hash_id)
    db.add(new_fav)
    db.commit()
    db.refresh(new_fav)
    return new_fav

@app.delete("/api/favorites/{hash_id}")
async def remove_favorite(hash_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    fav = db.query(models.Favorite).filter(
        models.Favorite.user_id == current_user.id,
        models.Favorite.hash_id == hash_id
    ).first()
    if fav:
        db.delete(fav)
        db.commit()
    return {"message": "Removed"}


def _normalize_dir_path(path: str) -> str:
    """Validate a directory path: must be inside BOOKS_DIR, must exist, must
    actually be a directory. Returns the canonical relative path (forward
    slashes, no trailing slash)."""
    if path is None:
        raise HTTPException(status_code=400, detail="Invalid path")
    rel = path.strip().lstrip("/").rstrip("/")
    target = os.path.abspath(os.path.join(BOOKS_DIR, rel))
    if not target.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="Directory not found")
    return rel


@app.get("/api/dir-favorites")
async def get_dir_favorites(
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    if current_user is None:  # guest — no per-user state
        return {"items": []}
    rows = db.query(models.DirectoryFavorite).filter(
        models.DirectoryFavorite.user_id == current_user.id
    ).order_by(models.DirectoryFavorite.path).all()
    items = []
    for row in rows:
        full = os.path.abspath(os.path.join(BOOKS_DIR, row.path))
        items.append({
            "id": row.id,
            "path": row.path,
            "name": os.path.basename(row.path) or row.path,
            "exists": full.startswith(os.path.abspath(BOOKS_DIR)) and os.path.isdir(full),
        })
    return {"items": items}


@app.post("/api/dir-favorites", response_model=schemas.DirectoryFavoriteResponse)
async def add_dir_favorite(
    fav: schemas.DirectoryFavoriteCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rel = _normalize_dir_path(fav.path)
    existing = db.query(models.DirectoryFavorite).filter(
        models.DirectoryFavorite.user_id == current_user.id,
        models.DirectoryFavorite.path == rel,
    ).first()
    if existing:
        return existing
    new_fav = models.DirectoryFavorite(user_id=current_user.id, path=rel)
    db.add(new_fav)
    db.commit()
    db.refresh(new_fav)
    return new_fav


@app.delete("/api/dir-favorites")
async def remove_dir_favorite(
    path: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # No existence check — let users remove a stale bookmark for a deleted dir.
    rel = path.strip().lstrip("/").rstrip("/")
    row = db.query(models.DirectoryFavorite).filter(
        models.DirectoryFavorite.user_id == current_user.id,
        models.DirectoryFavorite.path == rel,
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return {"message": "Removed"}

@app.get("/api/progress/{hash_id}", response_model=schemas.ReadingProgressResponse)
async def get_progress(hash_id: str, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if current_user is None:  # guest — no saved progress
        raise HTTPException(status_code=404, detail="No progress found")
    if not _is_admin(current_user) and _book_clearance(hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    progress = db.query(models.ReadingProgress).filter(
        models.ReadingProgress.user_id == current_user.id,
        models.ReadingProgress.hash_id == hash_id
    ).first()
    if not progress:
        raise HTTPException(status_code=404, detail="No progress found")
    return progress

@app.post("/api/progress", response_model=schemas.ReadingProgressResponse)
async def update_progress(prog: schemas.ReadingProgressCreate, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if current_user is None:  # guest — accept silently, nothing is persisted
        return {"id": 0, "user_id": 0, "hash_id": prog.hash_id, "location": prog.location}
    if not _is_admin(current_user) and _book_clearance(prog.hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    existing = db.query(models.ReadingProgress).filter(
        models.ReadingProgress.user_id == current_user.id,
        models.ReadingProgress.hash_id == prog.hash_id
    ).first()
    if existing:
        existing.location = prog.location
        db.commit()
        db.refresh(existing)
        return existing

    new_prog = models.ReadingProgress(user_id=current_user.id, hash_id=prog.hash_id, location=prog.location)
    db.add(new_prog)
    db.commit()
    db.refresh(new_prog)
    return new_prog

# ==============================================================================
# Ratings
# ==============================================================================
# A 1-5 star rating, one per user per book. Not moderated — it counts toward the
# book's average as soon as it is submitted.

@app.get("/api/books/{hash_id}/rating", response_model=schemas.RatingResponse)
async def get_my_rating(hash_id: str, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if current_user is None:  # guest — no personal rating
        return {"hash_id": hash_id, "rating": None}
    if not _is_admin(current_user) and _book_clearance(hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    row = db.query(models.BookRating).filter(
        models.BookRating.user_id == current_user.id,
        models.BookRating.hash_id == hash_id,
    ).first()
    return {"hash_id": hash_id, "rating": row.rating if row else None}


@app.post("/api/books/{hash_id}/rating", response_model=schemas.RatingResponse)
async def set_my_rating(hash_id: str, payload: schemas.RatingCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.rating < 1 or payload.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    if not current_user.is_admin and _book_clearance(hash_id, db) > (current_user.clearance or 0):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not db.query(models.Book.id).filter(models.Book.id == hash_id).first():
        raise HTTPException(status_code=404, detail="Book not found")
    now = _now_iso()
    row = db.query(models.BookRating).filter(
        models.BookRating.user_id == current_user.id,
        models.BookRating.hash_id == hash_id,
    ).first()
    if row:
        row.rating = payload.rating
        row.updated_at = now
    else:
        row = models.BookRating(
            user_id=current_user.id, hash_id=hash_id, rating=payload.rating,
            created_at=now, updated_at=now,
        )
        db.add(row)
    db.commit()
    return {"hash_id": hash_id, "rating": payload.rating}


@app.delete("/api/books/{hash_id}/rating")
async def delete_my_rating(hash_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(models.BookRating).filter(
        models.BookRating.user_id == current_user.id,
        models.BookRating.hash_id == hash_id,
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return {"message": "Removed"}


# ==============================================================================
# Comments
# ==============================================================================
# Text comments are moderated: they are created 'pending' and become public only
# after an admin approves them. A comment may have one level of replies.

_COMMENT_MAX_LEN = 5000
MODERATION_DIGEST_INTERVAL_HOURS = 6
_MODERATION_META_KEY = "moderation_email_last_sent_at"


def _author_name(email: str, real_name: str | None = None) -> str:
    """Public display name for a commenter — their chosen real name if they set
    one, otherwise the local-part of their email (so we never expose the full
    address to other users)."""
    if real_name and real_name.strip():
        return real_name.strip()
    return (email or "user").split("@", 1)[0]


def _maybe_send_moderation_digest(db: Session) -> None:
    """Best-effort: if pending comments exist and >6h elapsed since the last
    digest, email the admin a summary. Race-free — the conditional UPDATE on
    app_meta succeeds for exactly one of any concurrent callers."""
    try:
        pending = db.query(func.count(models.BookComment.id)).filter(
            models.BookComment.status == "pending"
        ).scalar() or 0
        if pending <= 0:
            return
        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        cutoff_iso = (now - timedelta(hours=MODERATION_DIGEST_INTERVAL_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Seed the throttle row once so the conditional UPDATE has something to match.
        if not db.query(models.AppMeta).filter(models.AppMeta.key == _MODERATION_META_KEY).first():
            try:
                db.add(models.AppMeta(key=_MODERATION_META_KEY, value="1970-01-01T00:00:00Z"))
                db.commit()
            except Exception:
                db.rollback()
        updated = db.query(models.AppMeta).filter(
            models.AppMeta.key == _MODERATION_META_KEY,
            models.AppMeta.value < cutoff_iso,
        ).update({models.AppMeta.value: now_iso}, synchronize_session=False)
        db.commit()
        if updated == 1:
            threading.Thread(
                target=email_utils.send_moderation_digest, args=(pending,), daemon=True,
            ).start()
    except Exception as e:
        print(f"moderation digest check failed: {e}")


@app.get("/api/books/{hash_id}/comments")
async def get_comments(hash_id: str, current_user: models.User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    if not _is_admin(current_user) and _book_clearance(hash_id, db) > _clearance_of(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")

    # Guests (uid None) see only approved comments and own nothing.
    uid = current_user.id if current_user else None
    visible = or_(
        models.BookComment.status == "approved",
        models.BookComment.user_id == uid,
    )
    tops = db.query(models.BookComment).filter(
        models.BookComment.hash_id == hash_id,
        models.BookComment.parent_id.is_(None),
        visible,
    ).order_by(models.BookComment.created_at).all()

    replies_by_parent: dict[int, list] = {}
    top_ids = [c.id for c in tops]
    if top_ids:
        for r in db.query(models.BookComment).filter(
            models.BookComment.parent_id.in_(top_ids),
            visible,
        ).order_by(models.BookComment.created_at).all():
            replies_by_parent.setdefault(r.parent_id, []).append(r)

    # Author display names + each top-level author's own rating of this book.
    all_uids = {c.user_id for c in tops}
    for reps in replies_by_parent.values():
        all_uids.update(r.user_id for r in reps)
    names = {}
    if all_uids:
        names = {uid: _author_name(email, real_name) for uid, email, real_name in db.query(
            models.User.id, models.User.email, models.User.real_name
        ).filter(models.User.id.in_(all_uids)).all()}
    ratings = {}
    if tops:
        ratings = dict(db.query(models.BookRating.user_id, models.BookRating.rating).filter(
            models.BookRating.hash_id == hash_id,
            models.BookRating.user_id.in_([c.user_id for c in tops]),
        ).all())

    def node(c, with_rating: bool) -> dict:
        return {
            "id": c.id,
            "author_name": names.get(c.user_id, "user"),
            "body": c.body,
            "status": c.status,
            "created_at": c.created_at,
            "is_own": c.user_id == uid,
            "rating": ratings.get(c.user_id) if with_rating else None,
            "replies": [node(r, False) for r in replies_by_parent.get(c.id, [])],
        }

    return {"comments": [node(c, True) for c in tops]}


@app.post("/api/books/{hash_id}/comments")
async def create_comment(hash_id: str, payload: schemas.CommentCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin and _book_clearance(hash_id, db) > (current_user.clearance or 0):
        raise HTTPException(status_code=403, detail="Forbidden")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment body required")
    if len(body) > _COMMENT_MAX_LEN:
        raise HTTPException(status_code=400, detail="Comment is too long")
    if not db.query(models.Book.id).filter(models.Book.id == hash_id).first():
        raise HTTPException(status_code=404, detail="Book not found")

    if payload.parent_id is not None:
        parent = db.query(models.BookComment).filter(
            models.BookComment.id == payload.parent_id).first()
        if not parent or parent.hash_id != hash_id:
            raise HTTPException(status_code=400, detail="Invalid parent comment")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="Cannot reply to a reply")
    else:
        existing = db.query(models.BookComment.id).filter(
            models.BookComment.hash_id == hash_id,
            models.BookComment.user_id == current_user.id,
            models.BookComment.parent_id.is_(None),
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="You have already commented on this book")

    now = _now_iso()
    # Admin comments need no moderation — they go live immediately.
    status = "approved" if current_user.is_admin else "pending"
    row = models.BookComment(
        user_id=current_user.id, hash_id=hash_id, parent_id=payload.parent_id,
        body=body, status=status, created_at=now, updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if row.status == "pending":
        _maybe_send_moderation_digest(db)
    return {"id": row.id, "status": row.status}


@app.put("/api/comments/{comment_id}")
async def edit_comment(comment_id: int, payload: schemas.CommentUpdate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")
    if row.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment body required")
    if len(body) > _COMMENT_MAX_LEN:
        raise HTTPException(status_code=400, detail="Comment is too long")
    row.body = body
    # Admin edits stay public; everyone else's edit returns to moderation.
    row.status = "approved" if current_user.is_admin else "pending"
    row.updated_at = _now_iso()
    db.commit()
    if row.status == "pending":
        _maybe_send_moderation_digest(db)
    return {"id": row.id, "status": row.status}


@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if not row:
        return {"message": "Removed"}
    if row.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    # No DB cascade is enforced — drop replies of a top-level comment by hand.
    if row.parent_id is None:
        db.query(models.BookComment).filter(models.BookComment.parent_id == row.id).delete()
    db.delete(row)
    db.commit()
    return {"message": "Removed"}


# ---------- Admin: comment moderation ----------

@app.get("/api/admin/comments")
async def admin_list_comments(
    status: str = "pending",
    page: int = 1,
    per_page: int = 50,
    _admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))

    q = db.query(models.BookComment)
    if status == "pending":
        q = q.filter(models.BookComment.status == "pending")
    elif status in ("approved", "rejected"):
        q = q.filter(models.BookComment.status == status)
    # status == "recent" (or anything else): no status filter, newest first.

    total = q.order_by(None).count()
    rows = (
        q.order_by(models.BookComment.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    book_ids = {r.hash_id for r in rows}
    titles = dict(db.query(models.Book.id, models.Book.title).filter(
        models.Book.id.in_(book_ids)).all()) if book_ids else {}
    # One reachable path per book so the admin can open it from the queue.
    paths: dict[str, str] = {}
    if book_ids:
        for hid, sp in db.query(
            models.BookLocation.hash_id, models.BookLocation.symlink_path
        ).filter(models.BookLocation.hash_id.in_(book_ids)).all():
            paths.setdefault(hid, sp)
    user_ids = {r.user_id for r in rows}
    names = {uid: _author_name(email, real_name) for uid, email, real_name in db.query(
        models.User.id, models.User.email, models.User.real_name
    ).filter(models.User.id.in_(user_ids)).all()} if user_ids else {}
    parent_ids = {r.parent_id for r in rows if r.parent_id}
    parents = dict(db.query(models.BookComment.id, models.BookComment.body).filter(
        models.BookComment.id.in_(parent_ids)).all()) if parent_ids else {}

    return {
        "comments": [
            {
                "id": r.id,
                "hash_id": r.hash_id,
                "book_title": titles.get(r.hash_id),
                "book_path": paths.get(r.hash_id),
                "author_name": names.get(r.user_id, "user"),
                "body": r.body,
                "status": r.status,
                "parent_id": r.parent_id,
                "parent_snippet": (parents.get(r.parent_id) or "")[:120] if r.parent_id else None,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


@app.post("/api/admin/comments/{comment_id}/approve")
async def admin_approve_comment(comment_id: int, _admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")
    row.status = "approved"
    row.updated_at = _now_iso()
    db.commit()
    return {"id": row.id, "status": row.status}


@app.delete("/api/admin/comments/{comment_id}")
async def admin_delete_comment(comment_id: int, _admin: models.User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(models.BookComment).filter(models.BookComment.id == comment_id).first()
    if row:
        if row.parent_id is None:
            db.query(models.BookComment).filter(models.BookComment.parent_id == row.id).delete()
        db.delete(row)
        db.commit()
    return {"message": "Removed"}


# Frontend static files will be mounted here later
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="frontend")
