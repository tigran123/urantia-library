"""Router module (extracted from main.py): the intelligent search subsystem —
the query tokenizer, the relevance scorer, the match-context snippet builder,
and the two search endpoints (paged results + bulk hash_id list). Moved verbatim
from main.py (no logic change)."""
import os
import re
import calendar
from html import unescape as _html_unescape
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, not_, func, case, select

import models
from database import get_db
from config import _escape_like
from deps import get_current_user, get_optional_user, _clearance_of, _is_admin
from serialize import _rating_stats, _attach_recommendations
from background import _record_usage_event

router = APIRouter()


def _m():
    """Lazy handle to the fully-imported `main` module. Tests redirect the
    library root via `monkeypatch.setattr(main, "BOOKS_DIR", ...)`, so this
    mutable runtime path is read through `main` (the patch target) rather than
    bound at import. main re-exports it from config, so production reads the same
    config value. Call-time import: no load-time cycle."""
    import main
    return main


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
# Attribute names paralleling _SEARCH_ALL_COLUMNS, used by _match_context to read
# the matched field value off a loaded Book without re-deriving the column.
_SEARCH_ALL_COLUMN_NAMES = [
    "title", "author", "description", "publisher", "series", "tags",
    "original_filename",
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
_STRUCTURAL_KEYS = {"path", "ext", "needs_review", "clearance", "added"}
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


# A present-but-unparseable `added:` value resolves to this sentinel cutoff so
# the filter matches nothing — never silently falling through to "no filter"
# and returning the whole library. It sorts lexicographically after any real
# ISO-8601 import_date (whose year is 4 digits), so `import_date >= sentinel` is
# always false.
_ADDED_NO_MATCH = "9999-12-31T23:59:59+00:00"

# An `added:` window so large it underflows the representable date range means
# "everything ever added" — clamp to a floor that sorts before every real
# import_date so the filter matches all, instead of overflowing.
_ADDED_MATCH_ALL = "0001-01-01T00:00:00+00:00"

# Fixed-length relative-window units accepted by `added:` → timedelta kwargs.
# Months ('m') and years ('y') are calendar-based (see _subtract_months), since
# they aren't a constant number of days.
_ADDED_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def _subtract_months(dt: datetime, months: int) -> datetime:
    """Return ``dt`` shifted back ``months`` calendar months, clamping the day to
    the target month's last day (e.g. 31 Mar − 1 month → 28/29 Feb)."""
    total = dt.year * 12 + (dt.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _parse_added_cutoff(val: str) -> str | None:
    """Parse an ``added:`` filter value into an ISO-8601 UTC lower-bound string.

    Accepts a relative window ``<N><unit>`` where unit ∈ ``h``/``d``/``w``/``m``/``y``
    (hours/days/weeks/months/years, e.g. ``7d``, ``6m``, ``1y``), or an absolute
    ``YYYY-MM-DD`` date. Returns ``None`` when unparseable. The window is computed
    relative to ``now`` so it matches the ``books_added_7d`` math in ``library_stats``."""
    val = (val or "").strip().lower()
    m = re.fullmatch(r"(\d+)\s*([hdwmy])", val)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        now = datetime.now(timezone.utc)
        try:
            if unit in _ADDED_UNITS:
                return (now - timedelta(**{_ADDED_UNITS[unit]: n})).isoformat()
            return _subtract_months(now, n if unit == "m" else n * 12).isoformat()
        except (OverflowError, ValueError):
            # `now - <window>` fell off the bottom of the representable range
            # (e.g. `added:99999y`); that just means "no effective lower bound".
            return _ADDED_MATCH_ALL
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", val)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            return None
        return dt.isoformat()
    return None


def parse_search_query(q: str):
    """Tokenize a raw query string.

    Returns ``(terms, filters)`` where ``filters`` holds the structural
    path/ext/needs_review filters and ``terms`` is a list of dicts:
    ``{text, field, negate, is_phrase}`` (text is lowercased)."""
    filters = {"path": None, "ext": [], "needs_review": None, "clearance": None, "added": None}
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
                # Multiple ext: tokens accumulate and are OR'd at query time
                # (ext:mp4 ext:mp3 → match either), deduped.
                e = val if val.startswith(".") else "." + val
                if e not in filters["ext"]:
                    filters["ext"].append(e)
            elif field == "needs_review":
                if val in ("1", "true", "yes"):
                    filters["needs_review"] = True
                elif val in ("0", "false", "no"):
                    filters["needs_review"] = False
            elif field == "clearance":
                m = re.fullmatch(r"(\d+)-(\d+)", val)
                if m:
                    n1, n2 = int(m.group(1)), int(m.group(2))
                    if n1 > n2:
                        n1, n2 = n2, n1
                    filters["clearance"] = ("between", n1, n2)
                else:
                    op = "eq"
                    num = val
                    if val.endswith("+"):
                        op, num = "gt", val[:-1].strip()
                    elif val.endswith("-"):
                        op, num = "lt", val[:-1].strip()
                    try:
                        n = int(num)
                    except ValueError:
                        continue
                    if n < 0:
                        continue
                    filters["clearance"] = (op, n, None)
            elif field == "added":
                # `added:7d` → ISO-8601 UTC lower bound; same window math as
                # /api/library-stats so a footer "added this week" click reproduces
                # that exact count. A non-empty but unparseable value (e.g.
                # `added:xyz`) resolves to a sentinel that matches nothing, rather
                # than silently dropping the filter and returning every book.
                filters["added"] = _parse_added_cutoff(val) or _ADDED_NO_MATCH
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


# Fields used to justify a search hit, in the order we prefer to show context
# from. Excludes title/author — those are always rendered in full on the card,
# so a match there needs no snippet.
_CONTEXT_FIELD_ORDER = ["description", "series", "tags", "publisher", "original_filename"]


def _term_regex(term: dict) -> "re.Pattern | None":
    """Compile a positive term to a regex mirroring its SQL ``LIKE`` semantics,
    used to LOCATE the match inside a field value so the snippet anchors exactly
    where the search matched. For non-phrase tokens `*`→`.*?` and `?`→`.` (SQL's
    `%`/`_`); phrases and the literal runs between wildcards are matched
    verbatim. Returns None when there's no literal text to anchor on (e.g. a bare
    ``*``). ``term['text']`` is already lowercased; the field value is matched
    case-insensitively, so this stays consistent with the LIKE query."""
    text = term["text"]
    if not text:
        return None
    if term["is_phrase"]:
        return re.compile(re.escape(text), re.IGNORECASE | re.DOTALL)
    parts = re.split(r"([*?])", text)
    if not any(p and p not in ("*", "?") for p in parts):
        return None
    frag = "".join(
        ".*?" if p == "*" else "." if p == "?" else re.escape(p)
        for p in parts
    )
    return re.compile(frag, re.IGNORECASE | re.DOTALL)


def _term_matches_field(rx: "re.Pattern", term: dict, field: str, book: models.Book) -> bool:
    """True if this term matches ``field`` on ``book`` (scope-aware). A
    `field:`-scoped term only counts against its own field. Matches the RAW field
    value, exactly like the SQL query — only the snippet display is cleaned."""
    if term["field"] is not None and term["field"] != field:
        return False
    return rx.search(getattr(book, field, None) or "") is not None


# Strip pattern for an actual HTML/XML tag (applied only once a value is
# recognized as HTML by _HTML_HINT_RE): `<`, optional `/`, a letter-led tag name,
# optional attributes, optional self-closing `/`, `>`.
_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^>]*)?/?>")

# Heuristic: does this value actually contain HTML? Real HTML prose has a closing
# tag, a void element (<br>/<hr>), an unmistakable block opener, or a self-close.
# It deliberately excludes ambiguous inline names (<i>, <b>, <a>, <code>, <map>,
# …) and bracketed identifiers (<iostream>, <T>) from the *detector*, so literal
# bracketed text in non-HTML fields is left intact; once a value IS recognized as
# HTML, _TAG_RE then removes every tag within it.
_HTML_HINT_RE = re.compile(
    r"</[A-Za-z]"
    r"|<br\b|<hr\b"
    r"|<(?:p|div|h[1-6]|ul|ol|li|blockquote|pre|table|tr|td|th|section|article|figure)\b[^>]*>"
    r"|/>",
    re.IGNORECASE,
)


def _to_plain_text(text: str) -> str:
    """Reduce metadata to readable plain text for a snippet. Entities are always
    decoded; HTML tags are stripped ONLY when the value actually looks like HTML
    (see _HTML_HINT_RE). That gate preserves literal bracketed text in non-HTML
    fields — e.g. «<О Небе>», "#include <iostream>", or "2 < 3" — which the
    snippet would otherwise mangle. Entities are decoded first so an
    entity-encoded tag (&lt;p&gt;) is detected and stripped too."""
    t = _html_unescape(text or "")
    if _HTML_HINT_RE.search(t):
        t = _TAG_RE.sub(" ", t)
    return t


def _build_snippet(text: str, rx: "re.Pattern", radius: int = 48) -> str | None:
    """A ``…``-bracketed window of ``text`` around the first match of ``rx``,
    snapped to word boundaries. Returns the original-cased text, or None if the
    pattern isn't found. HTML is reduced to plain text and whitespace collapsed
    so the snippet renders as a single tidy line. When a wide ``*`` makes the
    matched span longer than the window, the middle is elided (``… … …``) so both
    the leading and trailing literals that satisfied the search stay visible."""
    flat = " ".join(_to_plain_text(text).split())
    if not flat:
        return None
    m = rx.search(flat)
    if m is None:
        return None
    n = len(flat)
    mstart, mend = m.start(), m.end()

    def clip(a: int, b: int, keep_lo: int, keep_hi: int) -> tuple[int, int]:
        """Snap outer bounds [a,b] to word boundaries without eating into the
        [keep_lo, keep_hi] span that must stay visible."""
        if a > 0:
            sp = flat.find(" ", a, keep_lo)
            if sp != -1:
                a = sp + 1
        if b < n:
            sp = flat.rfind(" ", keep_hi, b)
            if sp != -1:
                b = sp
        return a, b

    if mend - mstart <= 2 * radius:
        a, b = clip(max(0, mstart - radius), min(n, mend + radius), mstart, mend)
        return ("…" if a > 0 else "") + flat[a:b] + ("…" if b < n else "")

    # Matched span is long (a wide `*`): show both anchors with the middle elided.
    ha, hb = clip(max(0, mstart - radius), min(n, mstart + radius), mstart, mstart)
    ta, tb = clip(max(0, mend - radius), min(n, mend + radius), mend, mend)
    head = ("…" if ha > 0 else "") + flat[ha:hb]
    tail = flat[ta:tb] + ("…" if tb < n else "")
    return head + " … " + tail


def _match_context(book: models.Book, terms: list) -> dict | None:
    """For a search hit, return ``{field, text}`` explaining why it matched when
    the match isn't already visible in the always-shown title/author — otherwise
    None. Purely presentational; never affects which rows match or their rank."""
    positives = []
    for t in terms:
        if t["negate"]:
            continue
        rx = _term_regex(t)
        if rx is not None:
            positives.append((t, rx))
    if not positives:
        return None
    # Self-evident when every positive term already appears in title or author.
    if all(_term_matches_field(rx, t, "title", book) or _term_matches_field(rx, t, "author", book)
           for t, rx in positives):
        return None
    for t, rx in positives:
        if _term_matches_field(rx, t, "title", book) or _term_matches_field(rx, t, "author", book):
            continue
        for field in _CONTEXT_FIELD_ORDER:
            if _term_matches_field(rx, t, field, book):
                snippet = _build_snippet(getattr(book, field, None) or "", rx)
                if snippet:
                    return {"field": field, "text": snippet}
    return None


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
        # parse_search_query normalizes each ext to start with a dot; `*`/`?`
        # are wildcards, so `ext:*` matches every file with an extension.
        # Multiple ext: values are OR'd (ext:mp4 ext:mp3 → either).
        query = query.filter(or_(*[
            func.lower(models.BookLocation.symlink_path).like(f"%{_wildcard_escape(e)}", escape="\\")
            for e in filters["ext"]
        ]))

    if filters["needs_review"] is not None and _is_admin(current_user):
        query = query.filter(models.Book.needs_review == filters["needs_review"])

    if filters["clearance"] is not None:
        op, n1, n2 = filters["clearance"]
        if op == "eq":
            query = query.filter(models.Book.clearance == n1)
        elif op == "gt":
            query = query.filter(models.Book.clearance > n1)
        elif op == "lt":
            query = query.filter(models.Book.clearance < n1)
        elif op == "between":
            query = query.filter(models.Book.clearance.between(n1, n2))

    if filters["added"]:
        # Inclusive lower bound: `added:2026-06-04` should include a book imported
        # at exactly 2026-06-04T00:00:00. For relative windows the cutoff is a
        # sub-second instant no book lands on exactly, so `>=` vs `>` is a no-op.
        query = query.filter(models.Book.import_date >= filters["added"])

    return query, terms


@router.get("/api/search")
async def search(
    request: Request,
    q: str = "",
    page: int = 1,
    per_page: int = 50,
    sort: str = Query("relevance", pattern="^(relevance|size|directory|import_date)$"),
    direction: str = Query("desc", alias="dir", pattern="^(asc|desc)$"),
    cols: int = 1,
    current_user: models.User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    per_page = max(1, min(per_page, 200))
    cols = max(1, min(cols, 50))

    if not q:
        return {"matches": [], "page": page, "per_page": per_page, "total": 0, "total_pages": 0,
                "sort": sort, "dir": direction}

    query, terms = _build_search_query(q, current_user, db)

    # A book can live at several locations (CAS many-to-one), so results are
    # de-duplicated to one row per book (GROUP BY Book.id) and the full location
    # list is attached below. Path-based ordering/tiebreaks use a representative
    # path = MIN(symlink_path) — the book's alphabetically-first location.
    #
    # Build ORDER BY. `direction` only meaningfully affects size, directory and
    # import_date; relevance is always score-desc with a stable tiebreak.
    asc = direction == "asc"
    rep_path = func.min(models.BookLocation.symlink_path)
    order_cols: list = []
    if sort == "size":
        size_col = models.Book.size
        order_cols.append((size_col.asc() if asc else size_col.desc()).nulls_last())
        order_cols += [models.Book.title, models.Book.id, rep_path]
    elif sort == "import_date":
        # import_date is ISO-8601 UTC TEXT, so lexicographic order == chronological.
        d = models.Book.import_date
        order_cols.append(d.asc() if asc else d.desc())
        order_cols += [models.Book.title, models.Book.id, rep_path]
    elif sort == "directory":
        # Lexicographic ordering on the representative path puts same-parent
        # siblings adjacent (their shared prefix sorts together), and within a
        # group falls back to filename order — exactly what we want for
        # multi-volume sets. No SQL dirname() needed.
        order_cols.append(rep_path.asc() if asc else rep_path.desc())
        order_cols.append(models.Book.id)
    else:
        avg_rating = (
            select(func.avg(models.BookRating.rating))
            .where(models.BookRating.hash_id == models.Book.id)
            .correlate(models.Book)
            .scalar_subquery()
        )
        score = _relevance_score(terms)
        if score is not None:
            order_cols.append(score.desc())
        order_cols += [
            avg_rating.desc().nulls_last(),
            models.Book.title, models.Book.id, rep_path,
        ]

    # One row per distinct book, carrying its representative (ordering) path so
    # the displayed primary location matches the row's sort position even under a
    # path: filter — where MIN() runs over the *surviving* locations, which may
    # exclude the book's global-min path. The BookLocation join from
    # _build_search_query is retained for the MIN() and path:/ext: filters.
    book_query = query.with_entities(models.Book, rep_path.label("rep_path")).group_by(models.Book.id)

    # Page-boundary strategy. In directory mode + grid view (cols >= 2), pages
    # are computed so that the last directory on each page either ends naturally
    # at a row boundary or — when it would otherwise have a partial last row
    # AND the spillover items belong to the same directory — gets extended by
    # up to (cols-1) items to fill that row. This eliminates the visual ugliness
    # where, e.g., a directory of 10 items at 13-wide grid splits 8/2 across two
    # pages instead of 10/0. We do the walk in Python over a path-only
    # projection (one representative path per book) of the sorted result set; for
    # any realistic library this is microseconds and avoids a much more invasive
    # offset-based pagination.
    if sort == "directory" and cols >= 2:
        paths: list[str] = [
            row[0] for row in
            query.with_entities(rep_path)
                 .group_by(models.Book.id)
                 .order_by(*order_cols)
                 .all()
        ]
        total = len(paths)
        dirs = [os.path.dirname(p) for p in paths]
        boundaries: list[int] = [0]
        i = 0
        while i < total:
            j = min(i + per_page, total)
            if j < total:
                last_dir = dirs[j - 1]
                k = j - 1
                while k > i and dirs[k - 1] == last_dir:
                    k -= 1
                dir_in_page = j - k
                extra = 0
                while j + extra < total and dirs[j + extra] == last_dir:
                    extra += 1
                if extra > 0 and dir_in_page % cols != 0:
                    take = min(cols - (dir_in_page % cols), extra)
                    j += take
            boundaries.append(j)
            i = j
        total_pages = max(1, len(boundaries) - 1)
        if page > total_pages:
            books = []
        else:
            start_idx = boundaries[page - 1]
            end_idx = boundaries[page]
            books = (
                book_query.order_by(*order_cols)
                .offset(start_idx)
                .limit(end_idx - start_idx)
                .all()
            )
    else:
        total = query.with_entities(func.count(func.distinct(models.Book.id))).scalar() or 0
        total_pages = (total + per_page - 1) // per_page
        books = (
            book_query.order_by(*order_cols)
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

    # One round-trip for every location of the books on this page, so each match
    # can list all the directories its book lives in.
    locs_by_book: dict[str, list[str]] = {}
    if books:
        loc_rows = (
            db.query(models.BookLocation.hash_id, models.BookLocation.symlink_path)
            .filter(models.BookLocation.hash_id.in_([b.id for b, _ in books]))
            .all()
        )
        for hid, sp in loc_rows:
            locs_by_book.setdefault(hid, []).append(sp)
        for paths_list in locs_by_book.values():
            paths_list.sort()

    matches = []
    for book, rep_path_val in books:
        book_paths = locs_by_book.get(book.id) or []
        # Primary path = the representative used for ORDER BY (MIN over the
        # *filtered* locations), so name/path/parent_dir match the row's sort
        # position. `locations` below still lists every place the book lives.
        sym_path = rep_path_val or (book_paths[0] if book_paths else "")
        cover_fs_path = os.path.join(_m().BOOKS_DIR, ".data", "covers", f"{book.id}.jpg")
        cover_url = f"/api/covers/{book.id}" if os.path.exists(cover_fs_path) else None
        # books.size is populated at upload time and by backfill_sizes.py; a NULL
        # here means a row that hasn't been backfilled yet (rare). Falling back
        # to os.path.getsize would re-introduce the per-request stat we just
        # eliminated, so just return None and let the frontend hide the size.
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
            "match_context": _match_context(book, terms),
            "clearance": int(book.clearance or 0),
            "size": book.size,
            "import_date": book.import_date,
            "locations": [
                {"path": p, "parent_dir": os.path.dirname(p), "name": os.path.basename(p)}
                for p in book_paths
            ],
        })

    stats = _rating_stats(db, [m["hash_id"] for m in matches])
    for m in matches:
        s = stats.get(m["hash_id"])
        m["avg_rating"] = s["avg_rating"] if s else None
        m["rating_count"] = s["rating_count"] if s else 0

    _attach_recommendations(matches, db)

    # Record only non-empty searches; an empty `q` was short-circuited above.
    _record_usage_event(
        request, "search",
        user=current_user,
        extra={"q": q, "page": page, "total": int(total)},
    )

    return {
        "matches": matches,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "sort": sort,
        "dir": direction,
    }


@router.get("/api/search/hash_ids")
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

