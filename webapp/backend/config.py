"""Foundation module (extracted from main.py): paths, environment-derived
constants, the GeoIP reader, and small pure helpers (`_now_iso`,
`_geo_lookup`, `_detect_format`, `_effective_suffix`, `_text_inner_ext`,
`_is_recommended_path`). Leaf of the foundation DAG — imports nothing from the
other split modules. Keeps the import-time side effects it inherited from
main.py: it creates FEEDBACK_ATTACHMENT_DIR and AVATAR_DIR, and opens the
GeoIP reader once at import."""
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
import geoip2.database

BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")

DATA_DIR = os.path.join(BOOKS_DIR, ".data")

FEEDBACK_ATTACHMENT_DIR = os.environ.get(
    "FEEDBACK_ATTACHMENT_DIR", os.path.join(DATA_DIR, "feedback_attachments")
)
os.makedirs(FEEDBACK_ATTACHMENT_DIR, exist_ok=True)

# The Recommended/ pseudo-directory is managed exclusively by the
# recommend/unrecommend endpoints. It is browsable, but the generic
# file-management paths (upload, move, directory delete) must refuse it so it
# can't be populated, relocated, or destroyed out-of-band. It is NOT in
# _TOPDIR_SKIPLIST — we want it to appear in /api/browse — but it IS excluded
# from the upload tree picker (/api/admin/dirs).
RECOMMENDED_SUBDIR = "Recommended"
RECOMMENDED_DIR = os.path.join(BOOKS_DIR, RECOMMENDED_SUBDIR)

# Directory creation is deferred to `_lifespan` (so bare `import main` from
# maintenance scripts like reimport_orphans.py doesn't have a filesystem side
# effect) and to `_create_recommendation` (lazy first-use fallback).
_TOPDIR_SKIPLIST = {".claude", ".antigravitycli", ".vscode", ".data", "CLAUDE.md", "GEMINI.md", "urantia-library"}

def _is_recommended_path(rel: str) -> bool:
    """True if `rel` (a BOOKS_DIR-relative POSIX path) is the Recommended
    pseudo-directory or anything beneath it."""
    return rel == RECOMMENDED_SUBDIR or rel.startswith(RECOMMENDED_SUBDIR + "/")

# Offline IP→geo lookup for usage_events. The .mmdb is refreshed monthly by
# scripts/update_geoip.sh; the reader here is opened once at module load, so
# a fresh database only takes effect on the next service restart.
# A missing file is non-fatal: events still record with NULL geo.
GEOIP_DB_PATH = os.path.join(DATA_DIR, "geoip", "GeoLite2-City.mmdb")
try:
    _geoip_reader: Optional[geoip2.database.Reader] = (
        geoip2.database.Reader(GEOIP_DB_PATH) if os.path.exists(GEOIP_DB_PATH) else None
    )
    if _geoip_reader is None:
        print(f"WARN: GeoIP database not found at {GEOIP_DB_PATH}; "
              "usage events will record with NULL geo until "
              "scripts/update_geoip.sh seeds it.")
except Exception as e:
    print(f"WARN: failed to open GeoIP database at {GEOIP_DB_PATH}: {e}")
    _geoip_reader = None

def _geo_lookup(ip: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve an IP to (country_iso, city_name). Returns (None, None) for
    loopback, link-local, missing database, or any lookup error — never
    raises. Safe to call from the request path."""
    if not _geoip_reader or not ip:
        return (None, None)
    # Loopback / link-local short-circuit (no geo on dev or behind reverse
    # proxy that hasn't set XFF correctly).
    if ip.startswith("127.") or ip == "::1" or ip.startswith("fe80:"):
        return (None, None)
    try:
        r = _geoip_reader.city(ip)
        return (r.country.iso_code, r.city.name)
    except Exception:
        return (None, None)

ONLINE_WINDOW = timedelta(minutes=5)

# How often the _lifespan loop mirrors _last_seen into users.last_seen_at.
# MUST stay well under ONLINE_WINDOW: the online-count path prunes _last_seen
# entries older than ONLINE_WINDOW, so a shorter flush interval guarantees a
# user's final activity timestamp is persisted before it's pruned away.
_LAST_SEEN_FLUSH_INTERVAL_SECONDS = 60

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Minimum password length enforced server-side at the password-setting steps
# (/api/set-password and /api/reset-password). The frontend mirrors this for
# inline validation, but the server is the authority.
MIN_PASSWORD_LENGTH = 8

# Per-IP rate limit for the unauthenticated password-reset endpoints
# (/api/forgot-password and /api/reset-password). An in-process sliding-window
# backstop (see routers/auth._reset_rate_limited) — the front-line cap is the
# nginx `limit_req` on those routes, but the in-process limiter still protects
# dev / manual-uvicorn runs where nginx isn't in front. Generous enough that no
# legitimate human (or a shared NAT) trips it; tight enough to blunt bursts.
RESET_RL_MAX = 10          # max requests per window, per client IP
RESET_RL_WINDOW_S = 60     # rolling window, seconds


def _escape_like(text: str) -> str:
    """Escape SQL LIKE metacharacters in user text (used with ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

AVATAR_DIR = os.path.join(DATA_DIR, "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)

STAGING_DIR = os.path.join(DATA_DIR, "staging")
COVERS_DIR = os.path.join(DATA_DIR, "covers")

_STAGING_TTL_S = 3600
_MAX_UPLOAD_BYTES = 850 * 1024 * 1024
_MAX_COVER_BYTES = 5 * 1024 * 1024
_AVATAR_MAX_BYTES = 5 * 1024 * 1024

_ACCEPTED_BOOK_EXTS = {
    "fb2", "zip", "epub", "pdf", "djvu", "mobi", "azw", "azw3", "prc",
    "docx", "odt", "html", "rtf", "txt", "jpg", "jpeg",
    "mp3", "wav", "ogg", "flac", "m4a", "aac",
    "mp4", "webm", "mkv", "avi", "mov",
}
_AUDIO_EXTS = {"mp3", "wav", "ogg", "flac", "m4a", "aac"}
_VIDEO_EXTS = {"mp4", "webm", "mkv", "avi", "mov"}

def _detect_format(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".fb2.zip"):
        return "fb2.zip"
    if name.endswith(".txt.zip"):
        return "txt.zip"
    if name.endswith(".md.zip"):
        return "md.zip"
    if name.endswith(".markdown.zip"):
        return "markdown.zip"
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return ext

# Suffixes that must be treated as a single unit (a format wrapped in .zip),
# not split on the last dot. Mirrors the unzip-on-read handling for those formats.
_MULTI_SUFFIXES = (".fb2.zip", ".txt.zip", ".md.zip", ".markdown.zip",
                   ".html.zip", ".htm.zip")
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

CODE_EXTENSIONS = {
    ".py", ".c", ".cpp", ".h", ".hpp", ".js", ".ts", ".jsx", ".tsx",
    ".lua", ".sh", ".bash", ".rs", ".go", ".java", ".css", ".scss",
    ".json", ".xml", ".yaml", ".yml", ".sql", ".ini"
}

def _effective_suffix(name: str) -> str:
    """Filename suffix treating .fb2.zip/.txt.zip/.html.zip/etc as one unit.
    Returns the dotted suffix (e.g. '.pdf', '.fb2.zip') or '' when there's none."""
    low = name.lower()
    for suf in _MULTI_SUFFIXES:
        if low.endswith(suf):
            return suf
    return os.path.splitext(low)[1]

def _text_inner_ext(name: str) -> str:
    """Effective text extension, ignoring a trailing .zip wrapper.
    e.g. 'notes.txt.zip' -> '.txt', 'README.md' -> '.md'."""
    low = name.lower()
    if low.endswith(".zip"):
        low = low[:-4]
    return os.path.splitext(low)[1]

# Recording kill-switch. Admin can toggle which event kinds get inserted from
# /api/admin/usage/settings. Module-level set is replaced (not mutated)
# whenever the admin saves, so readers in _record_usage_event need no lock —
# CPython's GIL guarantees an atomic name rebind, and `in` on a set is O(1).
_USAGE_KINDS_ALL = ("page", "book_open", "search", "login", "register",
                    "recommend", "unrecommend",
                    "playlist_create", "playlist_delete",
                    "playlist_add_item", "playlist_remove_item",
                    "playlist_visibility", "playlist_share", "playlist_copy",
                    "playlist_link_copy")
