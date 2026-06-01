"""Add ON DELETE actions to user-referencing FKs and enable FK enforcement.

Background: this SQLite DB was always declared with ON DELETE CASCADE / SET NULL
clauses, but the app never set PRAGMA foreign_keys=ON, so they were no-ops and
delete handlers cleared child rows by hand. From schema v9 the app enables
enforcement (database.py), so the FKs must actually express the intended
semantics. This migration rebuilds the 11 tables whose user-referencing FK had
no ON DELETE action:

  CASCADE  (per-user data): favorites, reading_progress, directory_favorites,
           playlists, book_ratings, book_comments, feedback_threads.user_id,
           feedback_recipients.admin_id
  SET NULL (identity-bearing, column made NULLABLE): feedback_messages.author_id,
           book_recommendations.recommended_by, admin_audit_log.actor_user_id
  SET NULL (already nullable): feedback_threads.assigned_admin_id

Every FK that already declared CASCADE/SET NULL is reproduced verbatim so the
rebuilt tables match lib_schema.sql exactly.

Atomicity: PRAGMA foreign_keys is toggled OFF before any transaction (it cannot
change inside one). We then run in autocommit mode with an explicit BEGIN/COMMIT
and execute statements individually (NOT executescript, which commits any
pending transaction first). Any exception -- including the final
foreign_key_check guard -- hits ROLLBACK and leaves schema_version at 8 (the
runner already snapshotted to pre-migrate-*.bak).

Rebuild order is parents-before-children for readability only; with foreign_keys
OFF and id/PK values copied verbatim, order is immaterial (feedback_threads
parents feedback_recipients/feedback_messages; book_comments self-references).
"""
import sqlite3

# --- Comprehensive orphan cleanup. Runs AFTER the rebuild (so the three
#     SET-NULL columns are already nullable) inside a convergence loop, so it
#     stays correct regardless of which orphans a given host has. CASCADE FK ->
#     delete the child row; SET NULL FK -> null the column. On the prod copy this
#     clears 3 annotations + 5 usage_events; everything else is already clean.
_CLEANUP = [
    # Feedback (thread is the cascade root; user-owned) then its dependents.
    "DELETE FROM feedback_threads     WHERE user_id   NOT IN (SELECT id FROM users)",
    "DELETE FROM feedback_recipients  WHERE thread_id NOT IN (SELECT id FROM feedback_threads)",
    "DELETE FROM feedback_recipients  WHERE admin_id  NOT IN (SELECT id FROM users)",
    "DELETE FROM feedback_messages    WHERE thread_id NOT IN (SELECT id FROM feedback_threads)",
    "DELETE FROM feedback_attachments WHERE thread_id NOT IN (SELECT id FROM feedback_threads)",
    # Playlists (owner-owned) then their items.
    "DELETE FROM playlists      WHERE owner_id    NOT IN (SELECT id FROM users)",
    "DELETE FROM playlist_items WHERE playlist_id NOT IN (SELECT id FROM playlists)",
    "DELETE FROM playlist_items WHERE item_type = 'book' AND book_hash_id NOT IN (SELECT id FROM books)",
    # Comments: drop those whose user/book is gone, then replies orphaned thereby.
    "DELETE FROM book_comments WHERE user_id NOT IN (SELECT id FROM users) OR hash_id NOT IN (SELECT id FROM books)",
    "DELETE FROM book_comments WHERE parent_id IS NOT NULL AND parent_id NOT IN (SELECT id FROM book_comments)",
    # Other per-user / per-book child tables (CASCADE -> delete).
    "DELETE FROM favorites           WHERE user_id NOT IN (SELECT id FROM users) OR hash_id NOT IN (SELECT id FROM books)",
    "DELETE FROM reading_progress    WHERE user_id NOT IN (SELECT id FROM users) OR hash_id NOT IN (SELECT id FROM books)",
    "DELETE FROM directory_favorites WHERE user_id NOT IN (SELECT id FROM users)",
    "DELETE FROM book_ratings        WHERE user_id NOT IN (SELECT id FROM users) OR hash_id NOT IN (SELECT id FROM books)",
    "DELETE FROM annotations         WHERE user_id NOT IN (SELECT id FROM users) OR hash_id NOT IN (SELECT id FROM books)",
    "DELETE FROM book_recommendations WHERE hash_id NOT IN (SELECT id FROM books)",
    "DELETE FROM book_locations      WHERE hash_id NOT IN (SELECT id FROM books)",
    "DELETE FROM user_notification_prefs WHERE user_id NOT IN (SELECT id FROM users)",
    # SET NULL -> detach instead of delete (columns are nullable post-rebuild).
    "UPDATE feedback_threads     SET book_hash_id      = NULL WHERE book_hash_id      IS NOT NULL AND book_hash_id      NOT IN (SELECT id FROM books)",
    "UPDATE feedback_threads     SET assigned_admin_id = NULL WHERE assigned_admin_id IS NOT NULL AND assigned_admin_id NOT IN (SELECT id FROM users)",
    "UPDATE feedback_messages    SET author_id         = NULL WHERE author_id         IS NOT NULL AND author_id         NOT IN (SELECT id FROM users)",
    "UPDATE book_recommendations SET recommended_by    = NULL WHERE recommended_by    IS NOT NULL AND recommended_by    NOT IN (SELECT id FROM users)",
    "UPDATE admin_audit_log      SET actor_user_id     = NULL WHERE actor_user_id     IS NOT NULL AND actor_user_id     NOT IN (SELECT id FROM users)",
    "UPDATE usage_events         SET hash_id           = NULL WHERE hash_id           IS NOT NULL AND hash_id           NOT IN (SELECT id FROM books)",
    "UPDATE usage_events         SET user_id           = NULL WHERE user_id           IS NOT NULL AND user_id           NOT IN (SELECT id FROM users)",
]

# --- Table rebuilds. Kept as a readable script, then split on ';' (safe: no ';'
#     appears inside any literal) and executed one statement at a time so the
#     sqlite3 module's pre-DDL auto-commit can't break atomicity.
_REBUILD_SCRIPT = """
-- favorites: user_id -> CASCADE (hash_id already CASCADE)
ALTER TABLE favorites RENAME TO favorites__old;
CREATE TABLE favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, hash_id)
);
INSERT INTO favorites (id, user_id, hash_id, added_date)
    SELECT id, user_id, hash_id, added_date FROM favorites__old;
DROP TABLE favorites__old;

-- reading_progress: user_id -> CASCADE
ALTER TABLE reading_progress RENAME TO reading_progress__old;
CREATE TABLE reading_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    location VARCHAR NOT NULL,
    percent REAL,
    UNIQUE(user_id, hash_id)
);
INSERT INTO reading_progress (id, user_id, hash_id, location, percent)
    SELECT id, user_id, hash_id, location, percent FROM reading_progress__old;
DROP TABLE reading_progress__old;

-- directory_favorites: user_id -> CASCADE
ALTER TABLE directory_favorites RENAME TO directory_favorites__old;
CREATE TABLE directory_favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    path VARCHAR NOT NULL,
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, path)
);
INSERT INTO directory_favorites (id, user_id, path, added_date)
    SELECT id, user_id, path, added_date FROM directory_favorites__old;
DROP TABLE directory_favorites__old;
CREATE INDEX idx_directory_favorites_user ON directory_favorites(user_id);

-- playlists: owner_id -> CASCADE
ALTER TABLE playlists RENAME TO playlists__old;
CREATE TABLE playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    description TEXT,
    visibility VARCHAR NOT NULL DEFAULT 'private',
    kind VARCHAR NOT NULL DEFAULT 'normal',
    share_token VARCHAR UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO playlists (id, owner_id, name, description, visibility, kind, share_token, created_at, updated_at)
    SELECT id, owner_id, name, description, visibility, kind, share_token, created_at, updated_at FROM playlists__old;
DROP TABLE playlists__old;
CREATE INDEX idx_playlists_owner ON playlists(owner_id);
CREATE UNIQUE INDEX ix_playlists_one_bookshelf ON playlists(owner_id) WHERE kind = 'bookshelf';

-- book_ratings: user_id -> CASCADE
ALTER TABLE book_ratings RENAME TO book_ratings__old;
CREATE TABLE book_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, hash_id)
);
INSERT INTO book_ratings (id, user_id, hash_id, rating, created_at, updated_at)
    SELECT id, user_id, hash_id, rating, created_at, updated_at FROM book_ratings__old;
DROP TABLE book_ratings__old;
CREATE INDEX idx_book_ratings_hash ON book_ratings(hash_id);

-- book_comments: user_id -> CASCADE (parent_id, hash_id already CASCADE)
ALTER TABLE book_comments RENAME TO book_comments__old;
CREATE TABLE book_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES book_comments(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO book_comments (id, user_id, hash_id, parent_id, body, status, created_at, updated_at)
    SELECT id, user_id, hash_id, parent_id, body, status, created_at, updated_at FROM book_comments__old;
DROP TABLE book_comments__old;
CREATE UNIQUE INDEX ix_book_comments_one_toplevel ON book_comments(user_id, hash_id) WHERE parent_id IS NULL;
CREATE INDEX idx_book_comments_hash_status ON book_comments(hash_id, status);
CREATE INDEX idx_book_comments_parent ON book_comments(parent_id);

-- book_recommendations: recommended_by -> SET NULL + NULLABLE (hash_id already CASCADE)
ALTER TABLE book_recommendations RENAME TO book_recommendations__old;
CREATE TABLE book_recommendations (
    hash_id        VARCHAR PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    recommended_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    recommended_at TEXT    NOT NULL
);
INSERT INTO book_recommendations (hash_id, recommended_by, recommended_at)
    SELECT hash_id, recommended_by, recommended_at FROM book_recommendations__old;
DROP TABLE book_recommendations__old;
CREATE INDEX ix_book_recommendations_at ON book_recommendations(recommended_at DESC);

-- feedback_threads: user_id -> CASCADE, assigned_admin_id -> SET NULL (book_hash_id already SET NULL)
ALTER TABLE feedback_threads RENAME TO feedback_threads__old;
CREATE TABLE feedback_threads (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id         VARCHAR UNIQUE NOT NULL,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category          VARCHAR NOT NULL,
    book_subcategory  VARCHAR,
    subject           VARCHAR NOT NULL,
    status            VARCHAR NOT NULL DEFAULT 'new',
    book_hash_id      VARCHAR REFERENCES books(id) ON DELETE SET NULL,
    book_page         INTEGER,
    diag              TEXT,
    assigned_admin_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    digested_at       TEXT,
    archived_at       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
INSERT INTO feedback_threads (id, public_id, user_id, category, book_subcategory, subject, status, book_hash_id, book_page, diag, assigned_admin_id, digested_at, archived_at, created_at, updated_at)
    SELECT id, public_id, user_id, category, book_subcategory, subject, status, book_hash_id, book_page, diag, assigned_admin_id, digested_at, archived_at, created_at, updated_at FROM feedback_threads__old;
DROP TABLE feedback_threads__old;
CREATE INDEX idx_feedback_threads_user     ON feedback_threads(user_id);
CREATE INDEX idx_feedback_threads_status   ON feedback_threads(status);
CREATE INDEX idx_feedback_threads_book     ON feedback_threads(book_hash_id);
CREATE INDEX idx_feedback_threads_digested ON feedback_threads(digested_at);

-- feedback_recipients: admin_id -> CASCADE (thread_id already CASCADE)
ALTER TABLE feedback_recipients RENAME TO feedback_recipients__old;
CREATE TABLE feedback_recipients (
    thread_id  INTEGER NOT NULL REFERENCES feedback_threads(id) ON DELETE CASCADE,
    admin_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (thread_id, admin_id)
);
INSERT INTO feedback_recipients (thread_id, admin_id)
    SELECT thread_id, admin_id FROM feedback_recipients__old;
DROP TABLE feedback_recipients__old;
CREATE INDEX idx_feedback_recipients_admin ON feedback_recipients(admin_id);

-- feedback_messages: author_id -> SET NULL + NULLABLE (thread_id already CASCADE)
ALTER TABLE feedback_messages RENAME TO feedback_messages__old;
CREATE TABLE feedback_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   INTEGER NOT NULL REFERENCES feedback_threads(id) ON DELETE CASCADE,
    author_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    kind        VARCHAR NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
INSERT INTO feedback_messages (id, thread_id, author_id, kind, body, created_at)
    SELECT id, thread_id, author_id, kind, body, created_at FROM feedback_messages__old;
DROP TABLE feedback_messages__old;
CREATE INDEX idx_feedback_messages_thread ON feedback_messages(thread_id);

-- admin_audit_log: actor_user_id -> SET NULL + NULLABLE
ALTER TABLE admin_audit_log RENAME TO admin_audit_log__old;
CREATE TABLE admin_audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action        VARCHAR NOT NULL,
    target_kind   VARCHAR,
    target_id     VARCHAR,
    summary       VARCHAR NOT NULL,
    details_json  TEXT
);
INSERT INTO admin_audit_log (id, created_at, actor_user_id, action, target_kind, target_id, summary, details_json)
    SELECT id, created_at, actor_user_id, action, target_kind, target_id, summary, details_json FROM admin_audit_log__old;
DROP TABLE admin_audit_log__old;
CREATE INDEX idx_admin_audit_log_created_at ON admin_audit_log(created_at);
CREATE INDEX idx_admin_audit_log_actor      ON admin_audit_log(actor_user_id);
CREATE INDEX idx_admin_audit_log_action     ON admin_audit_log(action);
"""


def upgrade(conn: sqlite3.Connection) -> None:
    prev_isolation = conn.isolation_level
    conn.isolation_level = None  # autocommit: take full manual control of txns
    conn.execute("PRAGMA foreign_keys = OFF")  # legal only outside a transaction
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("could not disable foreign_keys for the rebuild")
    # Modern SQLite's `ALTER TABLE x RENAME TO x__old` also rewrites references to
    # x in OTHER tables' FK definitions (playlist_items->playlists,
    # feedback_attachments->feedback_threads — neither is rebuilt here). We
    # rename the parent aside and recreate it under the same name, so that
    # rewrite would leave those children pointing at the dropped *__old table.
    # legacy_alter_table=ON restores pre-3.25 behaviour (rename the table only),
    # so the children keep referencing the recreated table.
    conn.execute("PRAGMA legacy_alter_table = ON")

    rebuild = [s.strip() for s in _REBUILD_SCRIPT.split(";") if s.strip()]
    conn.execute("BEGIN")
    try:
        # 1. Rebuild the 11 tables (plain copy; the 3 SET-NULL columns become
        #    nullable, so the cleanup below can null any dangling refs).
        for stmt in rebuild:
            conn.execute(stmt)
        # 2. Clean orphans across the WHOLE db, looping to convergence: deleting
        #    a parent can orphan a child (thread->messages, comment->replies,
        #    playlist->items). foreign_key_check is the exit test AND the guard.
        for _ in range(6):
            for stmt in _CLEANUP:
                conn.execute(stmt)
            if not conn.execute("PRAGMA foreign_key_check").fetchall():
                break
        else:
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            raise RuntimeError(f"orphan cleanup did not converge: {bad[:20]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.isolation_level = prev_isolation  # restore for the runner's version bump
