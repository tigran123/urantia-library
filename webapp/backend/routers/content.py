"""Router module (extracted from main.py): content delivery for every reader
format — raw file streaming (/api/files), FB2/Markdown/plain-text/source-code/
HTML conversion-to-HTML, DjVu metadata/outline/page rendering, and book covers.
All the format converters (`_convert_fb2`, `_convert_md`, `_convert_txt`,
`_convert_code`, `_convert_html`, `extract_djvu_outline`, …) live here; main.py
re-exports the handful that admin_uploads reaches via `main.<fn>`. Moved verbatim
from main.py (no logic change).

Every user-supplied path routes through a `sanitize_*_path` helper (→
`_safe_under_books`) and then `assert_can_read_path` before any filesystem op —
keep that ordering. Mutable runtime paths the tests monkeypatch on `main`
(BOOKS_DIR) are read via the call-time `_m()` shim."""
import os
import re
import io
import base64
import zipfile
import xml.etree.ElementTree as ET
import html.parser as _hp  # avoid clashing with the html.escape import below
from html import escape as _html_escape
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from PIL import Image
import djvu.decode
import djvu.sexpr

import models
from database import get_db
from config import CODE_EXTENSIONS, _AUDIO_EXTS, _VIDEO_EXTS, _text_inner_ext
from deps import get_optional_user, _clearance_of, _is_admin
from paths import (
    sanitize_fb2_path, sanitize_text_path, sanitize_html_path, sanitize_djvu_path,
    _safe_under_books, assert_can_read_path, _book_clearance,
)
from cas import _resolve_vault_hash, _read_text_bytes
from background import _record_usage_event

router = APIRouter()


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect the
    library root via `monkeypatch.setattr(main, "BOOKS_DIR", ...)`, so the
    mutable runtime path is read through `main` (the patch target). main
    re-exports it from config. Call-time import: no load-time cycle."""
    import main
    return main


@router.get("/api/files/{path:path}")
async def get_file(
    request: Request,
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    # Full traversal guard (realpath + skiplist), not just the lexical prefix
    # check: blocks downloading infra files (urantia-library/…, .data/db/lib.db,
    # secrets.env, …) and symlink-escape, both of which the weak guard allowed.
    file_path = _safe_under_books(path)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    assert_can_read_path(file_path, current_user, db)
    # Log the open, but don't let media streaming inflate the count: an <audio>/
    # <video> element issues many Range GETs per file (initial buffer + every seek),
    # so for A/V we count one "open" per play-from-start (no Range, or a Range that
    # begins at byte 0) and ignore the seek/continuation chunks. Non-media is logged
    # unconditionally as before. Server-side only — durations are derived at import,
    # so there is no client metadata-probe to exempt and no way to opt out.
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    is_media = ext in _AUDIO_EXTS or ext in _VIDEO_EXTS
    rng = (request.headers.get("range") or "").replace(" ", "")
    if not is_media or not rng or rng.startswith("bytes=0-"):
        _record_usage_event(
            request, "book_open",
            user=current_user,
            hash_id=_resolve_vault_hash(file_path),
            path=path,
        )
    return FileResponse(file_path)



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
    lang = ""
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
            lang_el = ti.find(_FB2_NS + "lang")
            if lang_el is not None and lang_el.text:
                lang = lang_el.text.strip()
    return {"title": title, "authors": authors, "lang": lang}


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
        "lang": meta["lang"],
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


@router.get("/api/fb2-content")
async def fb2_content(
    request: Request,
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
    _record_usage_event(
        request, "book_open",
        user=current_user,
        hash_id=_resolve_vault_hash(file_path),
        path=path,
    )
    return _convert_fb2(xml_bytes)


@router.get("/api/fb2-metadata")
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




def _read_text_file(file_path: str) -> str:
    raw = _read_text_bytes(file_path)
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


@router.get("/api/md-content")
async def md_content(
    request: Request,
    path: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    file_path = sanitize_text_path(path)
    assert_can_read_path(file_path, current_user, db)
    text = _read_text_file(file_path)
    inner = _text_inner_ext(file_path)
    _record_usage_event(
        request, "book_open",
        user=current_user,
        hash_id=_resolve_vault_hash(file_path),
        path=path,
    )
    if inner == ".txt":
        return _convert_txt(text)
    elif inner in CODE_EXTENSIONS:
        return _convert_code(text, inner[1:])
    return _convert_md(text)


@router.get("/api/text-preview")
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
    inner = _text_inner_ext(file_path)
    if inner != ".txt":
        if inner in CODE_EXTENSIONS:
            html = _render_code_snippet_html(snippet, inner[1:])
        else:
            html = _MdRenderer(collect_toc=False).render(snippet)
    return {
        "text": snippet,
        "html": html,
        "truncated": len(text) > len(snippet),
    }


# ---------------- HTML viewer ----------------



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


@router.get("/api/html-content")
async def html_content(
    request: Request,
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
    _record_usage_event(
        request, "book_open",
        user=current_user,
        hash_id=_resolve_vault_hash(file_path),
        path=path,
    )
    return _convert_html(data)


@router.get("/api/html-preview")
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




def _iter_djvu_outline_strings(node):
    """Yield the raw bytes of every string in a sexpr subtree."""
    for elem in node:
        if isinstance(elem, djvu.sexpr.ListExpression):
            yield from _iter_djvu_outline_strings(elem)
        elif isinstance(elem, djvu.sexpr.StringExpression):
            yield elem.bytes


def _djvu_outline_legacy(root) -> str:
    """Pick the fallback codepage for non-UTF-8 outline strings.

    Outline text is supposed to be UTF-8, but legacy scanners emit cp1251
    (Russian) or cp1252 (Western). Cyrillic cp1251 words show as *runs* of
    bytes >= 0xC0 (every Cyrillic letter lands there), while cp1252 accents
    and punctuation appear as isolated high bytes among ASCII. Require two
    consecutive-high-byte pairs before choosing cp1251 — a single pair can
    be a stray UTF-16 BOM entry (0xFE 0xFF) in an otherwise cp1252 file."""
    pairs = 0
    for raw in _iter_djvu_outline_strings(root):
        try:
            raw.decode("utf-8")
            continue
        except UnicodeDecodeError:
            pass
        prev_hi = False
        for b in raw:
            hi = b >= 0xC0
            if hi and prev_hi:
                pairs += 1
                if pairs >= 2:
                    return "cp1251"
            prev_hi = hi
    return "cp1252"


def _djvu_expr_text(expr, legacy: str) -> str:
    """Decode one sexpr string: UTF-8, then the document's legacy codepage,
    then latin-1 (which never fails)."""
    if not isinstance(expr, djvu.sexpr.StringExpression):
        return ""
    raw = expr.bytes
    for enc in ("utf-8", legacy):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _parse_djvu_bookmark(node, name_to_page: Dict[str, int], legacy: str) -> dict:
    """Convert one djvu sexpr bookmark node into a TOC entry.

    A node is a ListExpression (title, link, *children); `link` is
    "#<target>" where a numeric target is a 1-based page number (matching
    doc.pages[target-1]) and anything else is a component page name (e.g.
    "#default-848.djvu") resolved through `name_to_page`. Same resolution
    order as djvulibre: numeric first, then page id/name/title."""
    elems = list(node)
    title = _djvu_expr_text(elems[0], legacy) if len(elems) > 0 else ""
    link = _djvu_expr_text(elems[1], legacy) if len(elems) > 1 else ""
    page = None
    if link.startswith("#"):
        target = link[1:]
        page = int(target) if target.isdigit() else name_to_page.get(target)
    children = [_parse_djvu_bookmark(c, name_to_page, legacy)
                for c in elems[2:] if isinstance(c, djvu.sexpr.ListExpression)]
    return {"title": title, "page": page, "children": children}


def _djvu_page_names(doc) -> Dict[str, int]:
    """Map each component page's id/name/title to its 1-based page number.
    The digits embedded in a page name need not match its page number
    (components can be skipped), so a lookup table is the only correct way
    to resolve name links."""
    mapping: Dict[str, int] = {}
    try:
        for f in doc.files:
            if f.type != "P":
                continue
            for key in (f.id, f.name, f.title):
                if key:
                    mapping.setdefault(key, f.n_page + 1)
    except Exception:
        # A partial (or empty) map just leaves those entries unresolved
        # (page=None), matching the old behavior for name links.
        pass
    return mapping


def extract_djvu_outline(file_path: str) -> list:
    """Extract the embedded outline (bookmarks) of a DjVu file as a nested
    list of {title, page, children}. Returns [] when the file has none."""
    ctx = djvu.decode.Context()
    doc = ctx.new_document(djvu.decode.FileURI(file_path))
    doc.decoding_job.wait()
    outline = doc.outline
    outline.wait()
    items = list(outline.sexpr)  # [] when the file has no outline
    if not items:
        return []
    name_to_page = _djvu_page_names(doc)
    legacy = _djvu_outline_legacy(outline.sexpr)
    # items[0] is Symbol('bookmarks'); the rest are bookmark entries.
    return [_parse_djvu_bookmark(e, name_to_page, legacy) for e in items[1:]]


@router.get("/api/djvu-metadata")
async def djvu_metadata(
    request: Request,
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
        # Treat djvu-metadata as the book-open signal (one event per open);
        # /api/djvu-page is hit per-page-render and would flood the table.
        _record_usage_event(
            request, "book_open",
            user=current_user,
            hash_id=_resolve_vault_hash(file_path),
            path=path,
        )
        return {"total_pages": total_pages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/djvu-outline")
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

@router.get("/api/djvu-page")
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

@router.get("/api/covers/{hash_id}")
async def get_cover(
    hash_id: str,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    cover_path = os.path.join(_m().BOOKS_DIR, ".data", "covers", f"{hash_id}.jpg")
    if not os.path.exists(cover_path) or not os.path.isfile(cover_path):
        raise HTTPException(status_code=404, detail="Cover not found")
    if not _is_admin(current_user):
        required = _book_clearance(hash_id, db)
        if required > _clearance_of(current_user):
            raise HTTPException(status_code=403, detail="Forbidden")
    return FileResponse(cover_path)
