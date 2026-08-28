-- ==============================================================================
-- Urantia Library - Core Schema
-- Architecture: Hybrid Content-Addressable Storage (CAS)
-- ==============================================================================

-- 1. Users and Authentication
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email VARCHAR UNIQUE NOT NULL,
    hashed_password VARCHAR NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    clearance INTEGER NOT NULL DEFAULT 0,
    avatar_url VARCHAR,
    real_name VARCHAR,                  -- Optional display name shown on comments instead of the email local-part
    search_per_page INTEGER DEFAULT 50,
    accepted_legal_at TEXT,             -- ISO-8601 UTC; timestamp of the user's most-recent acceptance (any LEGAL_VERSION)
    legal_version_accepted TEXT,        -- LEGAL_VERSION at the time of accepted_legal_at; NULL or != current → re-prompt
    last_seen_at TEXT                   -- ISO-8601 UTC; last request time, flushed periodically from main._last_seen (NULL = never seen)
);

CREATE TABLE registration_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email VARCHAR UNIQUE NOT NULL,
    status VARCHAR,
    source VARCHAR,
    purpose VARCHAR,
    token VARCHAR UNIQUE,
    accepted_legal_at TEXT,             -- ISO-8601 UTC; stamped at submit. NOT NULL in /api/register's validation, but column-level nullable for forward-compat.
    language VARCHAR,                   -- ISO-639-1 ('en' | 'ru') captured at submit so the approval/rejection email matches the locale the requester used. NULL = legacy pre-0002 row, treated as English.
    legal_version_accepted TEXT         -- LEGAL_VERSION at submit; copied onto users.legal_version_accepted at approval
);

-- Persisted mirror of main.py's in-memory _active_sessions allowlist, so JWT
-- sessions survive a backend restart (rehydrated on startup by
-- _load_active_sessions). The in-memory dict stays the runtime source of truth;
-- rows are written on login and removed on logout / admin termination.
CREATE TABLE IF NOT EXISTS auth_sessions (
    jti          TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    ip_address   TEXT,
    user_agent   TEXT,
    created_at   TEXT NOT NULL,                                       -- ISO-8601 UTC
    last_seen_at TEXT NOT NULL,                                       -- ISO-8601 UTC
    expires_at   TEXT NOT NULL                                        -- ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user    ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires ON auth_sessions(expires_at);

-- One-time, expiring password-reset tokens (the "Forgot your password?" flow).
-- Only sha256(token) is stored, so a DB read can't reconstruct a live reset
-- link. Issued by POST /api/forgot-password, consumed once by
-- POST /api/reset-password (used_at stamped). user_id ON DELETE CASCADE reaps a
-- deleted user's tokens; _purge_expired_reset_tokens() sweeps expired/used rows
-- at startup, mirroring auth_sessions.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,                                        -- sha256(token) hex, 64 chars
    created_at  TEXT NOT NULL,                                        -- ISO-8601 UTC
    expires_at  TEXT NOT NULL,                                        -- ISO-8601 UTC
    used_at     TEXT                                                  -- ISO-8601 UTC; NULL = unused
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_password_reset_tokens_hash    ON password_reset_tokens(token_hash);
CREATE INDEX        IF NOT EXISTS ix_password_reset_tokens_user    ON password_reset_tokens(user_id);
CREATE INDEX        IF NOT EXISTS ix_password_reset_tokens_expires ON password_reset_tokens(expires_at);

-- ==============================================================================
-- 2. The CAS Metadata Vault
-- ==============================================================================
CREATE TABLE books (
    id VARCHAR PRIMARY KEY,             -- The BLAKE2b Hash of the physical file
    title VARCHAR,                      
    author VARCHAR,                     
    publisher VARCHAR,                  
    published VARCHAR,                  -- VARCHAR to accommodate '2003-05-15T00:00:00+00:00'
    description TEXT,                   -- Renamed from 'annotation' to match Vue UI
    tags VARCHAR,
    series VARCHAR,
    languages VARCHAR,
    identifiers VARCHAR,                -- e.g., 'isbn:5-271-05460-8'
    original_filename VARCHAR NOT NULL, -- Fallback for unparseable files
    needs_review BOOLEAN DEFAULT FALSE, -- Flag for manual librarian intervention
    clearance INTEGER NOT NULL DEFAULT 100, -- Minimum user clearance required to read; high by default so only admins see new books until classified down
    last_verified_at TEXT,              -- ISO-8601 UTC of last integrity check
    last_verified_ok BOOLEAN,           -- Result of last integrity check (NULL = never run)
    last_verified_mode VARCHAR,         -- 'quick' or 'full'
    last_verified_error VARCHAR,        -- Short failure key when last_verified_ok = FALSE
    import_date TEXT NOT NULL,          -- ISO-8601 UTC; source file mtime at migration time, or _now_iso() for webapp uploads
    size INTEGER,                       -- Byte size of /Books/.data/<id>; populated by backfill_sizes.py, NULL until then
    duration REAL,                      -- Audio/video length in seconds (ffprobe); set at import + backfill_durations.py, NULL until then
    bitrate INTEGER                     -- Audio/video bitrate in bits/sec (ffprobe); set at import + backfill_durations.py, NULL until then
);

CREATE INDEX idx_books_clearance ON books(clearance);
CREATE INDEX idx_books_size ON books(size);
CREATE INDEX idx_books_import_date ON books(import_date);

-- ==============================================================================
-- 3. The Spatial Search Index
-- ==============================================================================
CREATE TABLE book_locations (
    symlink_path VARCHAR PRIMARY KEY,   -- Primary Key prevents the SQLAlchemy 500 Error
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE
);

CREATE INDEX idx_book_locations_hash ON book_locations(hash_id);

-- ==============================================================================
-- 4. User Data (Strictly Hash-Based)
-- ==============================================================================
CREATE TABLE favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, hash_id)
);

CREATE TABLE reading_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    location VARCHAR NOT NULL,
    percent REAL,                       -- 0.0..1.0; NULL when not computable
    UNIQUE(user_id, hash_id)
);

CREATE TABLE directory_favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    path VARCHAR NOT NULL,
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, path)
);

CREATE INDEX idx_directory_favorites_user ON directory_favorites(user_id);

-- ==============================================================================
-- 4b. Playlists (supersede the single favourites list; books + directories as
--     first-class members). One kind='bookshelf' playlist per user is the
--     non-deletable default. share_token is stable, minted on first share.
-- ==============================================================================
CREATE TABLE playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    description TEXT,
    visibility VARCHAR NOT NULL DEFAULT 'private',   -- 'private' | 'public'
    kind VARCHAR NOT NULL DEFAULT 'normal',          -- 'normal' | 'bookshelf'
    share_token VARCHAR UNIQUE,
    created_at TEXT NOT NULL,                         -- ISO-8601 UTC
    updated_at TEXT NOT NULL                          -- ISO-8601 UTC
);

CREATE INDEX idx_playlists_owner ON playlists(owner_id);
-- One Bookshelf per user (race-safety on _get_or_create_bookshelf).
CREATE UNIQUE INDEX ix_playlists_one_bookshelf ON playlists(owner_id) WHERE kind = 'bookshelf';

CREATE TABLE playlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    item_type VARCHAR NOT NULL,                      -- 'book' | 'directory'
    book_hash_id VARCHAR REFERENCES books(id) ON DELETE CASCADE,
    dir_path VARCHAR,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL                            -- ISO-8601 UTC
);

CREATE UNIQUE INDEX ix_playlist_items_book ON playlist_items(playlist_id, book_hash_id) WHERE item_type = 'book';
CREATE UNIQUE INDEX ix_playlist_items_dir  ON playlist_items(playlist_id, dir_path)     WHERE item_type = 'directory';
CREATE INDEX ix_playlist_items_pos ON playlist_items(playlist_id, position);

-- ==============================================================================
-- 5. Ratings and Comments
-- ==============================================================================
-- One 1-5 star rating per user per book. Not moderated: a rating counts toward
-- the book's average the moment it is submitted.
CREATE TABLE book_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL,             -- 1..5
    created_at TEXT NOT NULL,            -- ISO-8601 UTC
    updated_at TEXT NOT NULL,            -- ISO-8601 UTC
    UNIQUE(user_id, hash_id)
);

CREATE INDEX idx_book_ratings_hash ON book_ratings(hash_id);

-- Moderated text comments. parent_id NULL = top-level comment; a non-NULL
-- parent_id is a reply (one level only -- replies cannot be replied to).
CREATE TABLE book_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES book_comments(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' (reject = row delete)
    created_at TEXT NOT NULL,            -- ISO-8601 UTC
    updated_at TEXT NOT NULL             -- ISO-8601 UTC
);

-- One top-level comment per user per book; replies are unrestricted.
CREATE UNIQUE INDEX ix_book_comments_one_toplevel
    ON book_comments(user_id, hash_id) WHERE parent_id IS NULL;
CREATE INDEX idx_book_comments_hash_status ON book_comments(hash_id, status);
CREATE INDEX idx_book_comments_parent ON book_comments(parent_id);

-- Admin-curated "recommended book" flag. A row here means the book has a
-- companion symlink under /Books/Recommended/ (registered in book_locations
-- like any other location). One row per recommended book; re-recommending
-- overwrites recommended_by + recommended_at.
CREATE TABLE book_recommendations (
    hash_id        VARCHAR PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    recommended_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    recommended_at TEXT    NOT NULL                       -- ISO-8601 UTC
);
CREATE INDEX ix_book_recommendations_at ON book_recommendations(recommended_at DESC);

-- Small key/value store for app-wide state (e.g. moderation digest throttle).
CREATE TABLE app_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- In-viewer annotations: highlights and notes anchored to a text selection.
-- Private annotations are stored with status='approved' (the field only gates
-- public visibility). When a user creates or edits a *public* annotation it
-- starts/returns to status='pending' so it goes through moderation.
CREATE TABLE annotations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id         VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    anchor          TEXT    NOT NULL,                    -- JSON; per-format selector (pdf/epub/html)
    selected_text   TEXT    NOT NULL,                    -- snapshot of the highlighted text
    text_prefix     TEXT,                                -- ~64 chars before, re-anchor fallback
    text_suffix     TEXT,                                -- ~64 chars after, re-anchor fallback
    body            TEXT,                                -- note text; NULL = plain highlight
    is_public       BOOLEAN NOT NULL DEFAULT 0,
    status          VARCHAR NOT NULL DEFAULT 'approved', -- 'pending' | 'approved'
    created_at      TEXT    NOT NULL,                    -- ISO-8601 UTC
    updated_at      TEXT    NOT NULL
);
CREATE INDEX idx_annotations_hash_status ON annotations(hash_id, status);
CREATE INDEX idx_annotations_user        ON annotations(user_id);

-- ==============================================================================
-- 6. Feedback / Contact-admin
-- ==============================================================================

-- A feedback thread. One per "ticket". Lives forever (never hard-deleted by
-- the user — admin can purge if needed). The full conversation hangs off
-- this row via feedback_messages.
CREATE TABLE feedback_threads (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id         VARCHAR UNIQUE NOT NULL,            -- 'UL-2026-0421' user-visible id
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category          VARCHAR NOT NULL,                   -- 'general'|'bug'|'feature'|'book'|'acquire'|'other'
    book_subcategory  VARCHAR,                            -- when category='book': 'metadata'|'corrupt'|'copyright'|'inappropriate'|'duplicate'
    subject           VARCHAR NOT NULL,
    status            VARCHAR NOT NULL DEFAULT 'new',     -- new|open|triage|progress|waiting|resolved|closed|archived
    book_hash_id      VARCHAR REFERENCES books(id) ON DELETE SET NULL,
    book_page         INTEGER,                            -- captured at submit, for PDF/DJVU
    diag              TEXT,                               -- JSON: { browser, viewport, route, build, locale } — never edited
    assigned_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    digested_at       TEXT,                               -- ISO-8601 UTC; NULL until included in a digest
    archived_at       TEXT,                               -- ISO-8601 UTC; non-null = archived
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL                       -- touched on every message, status change, etc.
);
CREATE INDEX idx_feedback_threads_user        ON feedback_threads(user_id);
CREATE INDEX idx_feedback_threads_status      ON feedback_threads(status);
CREATE INDEX idx_feedback_threads_book        ON feedback_threads(book_hash_id);
CREATE INDEX idx_feedback_threads_digested    ON feedback_threads(digested_at);

-- Optional recipient list. No rows ⇒ broadcast to every admin. Non-empty ⇒
-- only those admins see the thread and receive notification emails about it.
-- Sender may appear here (first-class self-reminder).
CREATE TABLE feedback_recipients (
    thread_id  INTEGER NOT NULL REFERENCES feedback_threads(id) ON DELETE CASCADE,
    admin_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (thread_id, admin_id)
);
CREATE INDEX idx_feedback_recipients_admin ON feedback_recipients(admin_id);

-- A single entry in a thread. Four kinds:
--   'message'   — author's original post or a follow-up reply
--   'admin'     — admin's public reply (visible to user)
--   'internal'  — admin-only note (never sent to user, never shown in user thread view)
--   'status'    — system event row for status changes (body holds the new status value)
CREATE TABLE feedback_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   INTEGER NOT NULL REFERENCES feedback_threads(id) ON DELETE CASCADE,
    author_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    kind        VARCHAR NOT NULL,                          -- 'message' | 'admin' | 'internal' | 'status'
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_feedback_messages_thread ON feedback_messages(thread_id);

-- One screenshot per thread (design spec allows only one). Stored on disk
-- under FEEDBACK_ATTACHMENT_DIR; this row is the metadata.
CREATE TABLE feedback_attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    INTEGER NOT NULL REFERENCES feedback_threads(id) ON DELETE CASCADE,
    filename     VARCHAR NOT NULL,        -- as uploaded
    stored_path  VARCHAR NOT NULL,        -- relative to FEEDBACK_ATTACHMENT_DIR
    content_type VARCHAR NOT NULL,
    bytes        INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);

-- Per-user email preferences. Absent row = all defaults = on.
CREATE TABLE user_notification_prefs (
    user_id              INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email_on_reply       BOOLEAN NOT NULL DEFAULT TRUE,
    email_on_status      BOOLEAN NOT NULL DEFAULT TRUE,
    email_weekly_summary BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at           TEXT NOT NULL
);

-- Admin-side digest configuration. Single row keyed by id=1. Seeded on startup.
CREATE TABLE admin_feedback_settings (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    digest_interval_hours    INTEGER NOT NULL DEFAULT 6,   -- 0 = off
    min_batch_size           INTEGER NOT NULL DEFAULT 1,
    urgent_bypass            BOOLEAN NOT NULL DEFAULT TRUE,
    extra_recipients         TEXT NOT NULL DEFAULT '',     -- comma-separated extra admin emails
    updated_at               TEXT NOT NULL
);

-- ==============================================================================
-- 7. Librarian Audit Log
-- ==============================================================================

-- Append-only log of administrative actions (book lifecycle, user management,
-- moderation decisions). Surfaced in the Admin → Audit Log panel as both a
-- timeline and a per-admin leaderboard. Rows are written via _audit() in
-- main.py inside the caller's transaction — a rolled-back action leaves no
-- audit ghost.
CREATE TABLE admin_audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,                       -- ISO-8601 UTC
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action        VARCHAR NOT NULL,                       -- 'book.upload' | 'book.edit' | 'book.delete' | 'book.move' | 'book.replace' | 'book.cover' | 'book.clearance' | 'book.recommend' | 'book.unrecommend' | 'user.clearance' | 'user.session_terminate' | 'comment.moderate' | 'annotation.moderate' | 'usage.kinds_update' | 'legal.accept' | 'password.reset_request' | 'password.reset_complete'
    target_kind   VARCHAR,                                -- 'book' | 'user' | 'comment' | 'annotation' | NULL
    target_id     VARCHAR,                                -- book hash, user id, comment id, … (VARCHAR holds either int or hash as text)
    summary       VARCHAR NOT NULL,                       -- one-line human-readable
    details_json  TEXT                                    -- optional structured payload (field diff, old/new values)
);
CREATE INDEX idx_admin_audit_log_created_at ON admin_audit_log(created_at);
CREATE INDEX idx_admin_audit_log_actor      ON admin_audit_log(actor_user_id);
CREATE INDEX idx_admin_audit_log_action     ON admin_audit_log(action);

-- ==============================================================================
-- 8. Usage Events (library analytics)
-- ==============================================================================
-- Per-request telemetry: page views, book opens, searches, logins, registrations.
-- Written from main.py via _record_usage_event() (try/except wrapped so telemetry
-- can never break a request). Read by the Admin → Usage panel and the per-user
-- My Activity page (#/account/activity).
--
-- IP address is personal data under GDPR (CJEU Breyer C-582/14). Retention is
-- 24 months, enforced daily by scripts/prune_usage_events.sh. See the Privacy
-- Policy for the user-facing data-flow description.
--
-- FK ondelete semantics:
--   * users(id) ON DELETE SET NULL  — account deletion detaches the timeline
--                                      from the identity without erasing it.
--   * books(id) ON DELETE SET NULL  — a book removal shouldn't erase the
--                                      historical fact that it was opened.
CREATE TABLE usage_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,                                       -- ISO-8601 UTC ('YYYY-MM-DDTHH:MM:SSZ')
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,     -- NULL = guest or detached
    session_jti  TEXT,                                                -- JWT jti, clusters one session's events
    ip           TEXT NOT NULL,                                       -- raw client IP, post-XFF
    user_agent   TEXT,                                                -- truncated to ~500 chars
    geo_country  TEXT,                                                -- ISO-2 (e.g. 'AM','RU') or NULL
    geo_city     TEXT,                                                -- English name from GeoLite2 or NULL
    kind         TEXT NOT NULL,                                       -- 'page'|'book_open'|'search'|'login'|'register'|'recommend'|'unrecommend'
    path         TEXT,                                                -- normalized request path or filtered ?path= value
    hash_id      TEXT REFERENCES books(id) ON DELETE SET NULL,        -- book hash for kind='book_open', else NULL
    extra_json   TEXT                                                 -- per-kind details (search query+count, login success, …)
);
CREATE INDEX ix_usage_events_ts      ON usage_events(ts);
CREATE INDEX ix_usage_events_user_ts ON usage_events(user_id, ts);
CREATE INDEX ix_usage_events_ip_ts   ON usage_events(ip, ts);
CREATE INDEX ix_usage_events_hash_ts ON usage_events(hash_id, ts);
CREATE INDEX ix_usage_events_kind_ts ON usage_events(kind, ts);

-- Schema version stamp. The runner at webapp/backend/migrate.py reads this
-- and applies any numbered files in webapp/backend/migrations/ whose number
-- is greater than the stored value. The backend refuses to start unless this
-- equals EXPECTED_SCHEMA_VERSION in database.py.
INSERT OR IGNORE INTO app_meta(key, value) VALUES ('schema_version', '16');
