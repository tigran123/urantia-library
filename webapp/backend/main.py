from fastapi import FastAPI, HTTPException, Request, Depends, Cookie, status, Response, UploadFile, File
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from fastapi.responses import JSONResponse, FileResponse, Response
import os
import re
import subprocess
import io
import hashlib
import zipfile
import base64
import shutil
import uuid
import xml.etree.ElementTree as ET
from html import escape as _html_escape
from PIL import Image
import djvu.decode
from typing import List, Dict, Any
from pathlib import Path
import json
import logging
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
import models
import schemas
from database import engine, get_db
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
        "search_per_page": current_user.search_per_page,
    }

@app.put("/api/users/me/settings", response_model=schemas.UserResponse)
async def update_settings(
    settings: schemas.UserSettingsUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if settings.search_per_page is not None:
        current_user.search_per_page = max(10, min(settings.search_per_page, 200))
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
async def browse(path: str = "", current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    target_dir = os.path.join(BOOKS_DIR, path)
    if not os.path.abspath(target_dir).startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="Directory not found")

    items = []

    try:
        entries = sorted(os.listdir(target_dir))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    for entry in entries:
        if entry in [".claude", ".vscode", ".data", "md5sums.txt", "urantia-library"]:
            continue

        entry_path = os.path.join(target_dir, entry)
        if not os.path.exists(entry_path):
            continue
        is_dir = os.path.isdir(entry_path)

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
                    if book.title:
                        item_data["title"] = book.title
                    if book.author:
                        item_data["author"] = book.author
                    if book.description:
                        item_data["description"] = book.description

        items.append(item_data)

    # Sort: folders first, then files
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    return {"path": path, "items": items}

def parse_search_query(q: str):
    filters = {
        "path": None,
        "ext": None,
    }

    path_match = re.search(r'path:([^\s]+)', q)
    if path_match:
        filters["path"] = path_match.group(1).strip('"\'').lower()
        q = q.replace(path_match.group(0), '')

    ext_match = re.search(r'ext:([^\s]+)', q)
    if ext_match:
        filters["ext"] = ext_match.group(1).strip('"\'').lower()
        if not filters["ext"].startswith('.'):
            filters["ext"] = '.' + filters["ext"]
        q = q.replace(ext_match.group(0), '')

    q = q.strip().lower()
    return q, filters

@app.get("/api/search")
async def search(
    q: str = "",
    page: int = 1,
    per_page: int = 50,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))

    if not q:
        return {"matches": [], "page": page, "per_page": per_page, "total": 0, "total_pages": 0}

    query_lower, filters = parse_search_query(q)

    query = db.query(models.Book, models.BookLocation).join(
        models.BookLocation, models.Book.id == models.BookLocation.hash_id
    )

    if query_lower:
        like = f"%{query_lower}%"
        query = query.filter(or_(
            func.lower(models.Book.title).like(like),
            func.lower(models.Book.author).like(like),
            func.lower(models.Book.description).like(like),
        ))

    if filters["path"]:
        query = query.filter(
            func.lower(models.BookLocation.symlink_path).like(f"{filters['path']}%")
        )

    if filters["ext"]:
        # parse_search_query normalizes ext to start with a dot.
        query = query.filter(
            func.lower(models.BookLocation.symlink_path).like(f"%{filters['ext']}")
        )

    total = query.order_by(None).count()
    total_pages = (total + per_page - 1) // per_page

    results = (
        query.order_by(models.Book.title, models.Book.id, models.BookLocation.symlink_path)
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
        })

    return {
        "matches": matches,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }

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
    new_user = models.User(
        email=db_req.email,
        hashed_password=hashed_password,
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

@app.get("/api/files/{path:path}")
async def get_file(path: str, current_user: models.User = Depends(get_current_user)):
    file_path = os.path.join(BOOKS_DIR, path)
    if not os.path.abspath(file_path).startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
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
async def fb2_content(path: str, current_user: models.User = Depends(get_current_user)):
    file_path = sanitize_fb2_path(path)
    try:
        xml_bytes = _read_fb2_bytes(file_path)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Corrupt zip archive")
    return _convert_fb2(xml_bytes)


@app.get("/api/fb2-metadata")
async def fb2_metadata(path: str, current_user: models.User = Depends(get_current_user)):
    file_path = sanitize_fb2_path(path)
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

def sanitize_text_path(path: str) -> str:
    """Ensure path is within BOOKS_DIR, exists, and is a .md/.markdown/.txt file."""
    if not path:
        raise HTTPException(status_code=400, detail="Invalid path")
    target_path = os.path.abspath(os.path.join(BOOKS_DIR, path))
    if not target_path.startswith(os.path.abspath(BOOKS_DIR)):
        raise HTTPException(status_code=403, detail="Directory traversal detected")
    if not os.path.exists(target_path) or not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="File not found")
    lower = target_path.lower()
    if not (lower.endswith(".md") or lower.endswith(".markdown") or lower.endswith(".txt")):
        raise HTTPException(status_code=400, detail="Not a Markdown or text file")
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
async def md_content(path: str, current_user: models.User = Depends(get_current_user)):
    file_path = sanitize_text_path(path)
    text = _read_text_file(file_path)
    if file_path.lower().endswith(".txt"):
        return _convert_txt(text)
    return _convert_md(text)


@app.get("/api/text-preview")
async def text_preview(
    path: str,
    max_chars: int = 2000,
    current_user: models.User = Depends(get_current_user),
):
    """Return up to max_chars of text from a .md/.txt file for use as a
    cover-slot placeholder preview. For .md the snippet is also rendered to
    HTML so the preview can mirror the in-viewer formatting. Clamp the limit
    to keep responses tiny."""
    file_path = sanitize_text_path(path)
    text = _read_text_file(file_path)
    limit = max(200, min(int(max_chars), 8000))
    snippet = text[:limit]
    html = ""
    if not file_path.lower().endswith(".txt"):
        # Render only the snippet; partial input is fine for a preview, the
        # frontend clips visually anyway.
        html = _MdRenderer(collect_toc=False).render(snippet)
    return {
        "text": snippet,
        "html": html,
        "truncated": len(text) > len(snippet),
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

@app.get("/api/djvu-metadata")
async def djvu_metadata(path: str, current_user: models.User = Depends(get_current_user)):
    file_path = sanitize_djvu_path(path)
    try:
        ctx = djvu.decode.Context()
        doc = ctx.new_document(djvu.decode.FileURI(file_path))
        doc.decoding_job.wait()
        total_pages = len(doc.pages)
        return {"total_pages": total_pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/djvu-page")
async def djvu_page(path: str, page: int, current_user: models.User = Depends(get_current_user)):
    file_path = sanitize_djvu_path(path)
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
async def get_cover(hash_id: str, current_user: models.User = Depends(get_current_user)):
    cover_path = os.path.join(BOOKS_DIR, ".data", "covers", f"{hash_id}.jpg")
    if not os.path.exists(cover_path) or not os.path.isfile(cover_path):
        raise HTTPException(status_code=404, detail="Cover not found")
    return FileResponse(cover_path)

@app.get("/api/favorites")
async def get_favorites(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    results = db.query(models.Favorite, models.Book, models.BookLocation).join(
        models.Book, models.Favorite.hash_id == models.Book.id
    ).outerjoin(
        models.BookLocation, models.Book.id == models.BookLocation.hash_id
    ).filter(models.Favorite.user_id == current_user.id).all()

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
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
async def get_progress(hash_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    progress = db.query(models.ReadingProgress).filter(
        models.ReadingProgress.user_id == current_user.id,
        models.ReadingProgress.hash_id == hash_id
    ).first()
    if not progress:
        raise HTTPException(status_code=404, detail="No progress found")
    return progress

@app.post("/api/progress", response_model=schemas.ReadingProgressResponse)
async def update_progress(prog: schemas.ReadingProgressCreate, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
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

# Frontend static files will be mounted here later
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="frontend")
