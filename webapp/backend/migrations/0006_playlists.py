"""Introduce the My Playlists feature and migrate the old single favourites
list into a per-user default "Bookshelf" playlist.

What this does, in order:
  1. Creates the `playlists` and `playlist_items` tables + indexes (matching
     lib_schema.sql, which carries the same DDL for fresh installs).
  2. For every existing user, creates one `kind='bookshelf'` playlist
     (visibility='private', name='Bookshelf') — the non-deletable default that
     supersedes My Bookshelf.
  3. Backfills that bookshelf from the user's existing per-user state:
       - `directory_favorites` rows  -> playlist_items(item_type='directory')
       - `favorites` rows            -> playlist_items(item_type='book')
     Directories get the LOWEST positions (shown at the top by default, per the
     product decision); books follow. Ordering within each group is by the old
     `added_date` so the historical save order is preserved.

The legacy `favorites` and `directory_favorites` tables are intentionally left
in place (dormant) — no drops. They are a one-time migration source and keep
this migration reversible by restoring the pre-migrate snapshot.

Idempotent: tables use IF NOT EXISTS, and a user who already has a bookshelf
(e.g. on a re-run) is skipped, so re-application doesn't duplicate rows.
"""
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    # Match the Z-suffixed format the app's _now_iso() writes at runtime, so
    # backfilled playlist timestamps are consistent with later edits.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upgrade(conn: sqlite3.Connection) -> None:
    # --- 1. Schema -----------------------------------------------------------
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id),
            name VARCHAR NOT NULL,
            description TEXT,
            visibility VARCHAR NOT NULL DEFAULT 'private',
            kind VARCHAR NOT NULL DEFAULT 'normal',
            share_token VARCHAR UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_playlists_owner ON playlists(owner_id);
        -- One Bookshelf per user. Created here (not just in 0008) so the
        -- guarantee holds even if a deploy halts between this migration and
        -- 0008 — otherwise _get_or_create_bookshelf's IntegrityError-catch
        -- path has no index to fire against and the duplicate-bookshelf race
        -- reopens. 0008 re-runs this idempotently for DBs already past v6.
        CREATE UNIQUE INDEX IF NOT EXISTS ix_playlists_one_bookshelf
            ON playlists(owner_id) WHERE kind = 'bookshelf';

        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            item_type VARCHAR NOT NULL,
            book_hash_id VARCHAR REFERENCES books(id) ON DELETE CASCADE,
            dir_path VARCHAR,
            position INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_playlist_items_book
            ON playlist_items(playlist_id, book_hash_id) WHERE item_type = 'book';
        CREATE UNIQUE INDEX IF NOT EXISTS ix_playlist_items_dir
            ON playlist_items(playlist_id, dir_path) WHERE item_type = 'directory';
        CREATE INDEX IF NOT EXISTS ix_playlist_items_pos
            ON playlist_items(playlist_id, position);
        """
    )

    # --- 2 & 3. Per-user bookshelf + backfill --------------------------------
    now = _now()
    user_ids = [r[0] for r in conn.execute("SELECT id FROM users").fetchall()]
    for uid in user_ids:
        existing = conn.execute(
            "SELECT id FROM playlists WHERE owner_id = ? AND kind = 'bookshelf'",
            (uid,),
        ).fetchone()
        if existing:
            continue  # idempotent re-run

        cur = conn.execute(
            "INSERT INTO playlists (owner_id, name, description, visibility, kind, "
            "share_token, created_at, updated_at) "
            "VALUES (?, 'Bookshelf', NULL, 'private', 'bookshelf', NULL, ?, ?)",
            (uid, now, now),
        )
        playlist_id = cur.lastrowid

        position = 0
        # Directories first (top), ordered by historical save order.
        dirs = conn.execute(
            "SELECT path FROM directory_favorites WHERE user_id = ? "
            "ORDER BY added_date, id",
            (uid,),
        ).fetchall()
        for (path,) in dirs:
            conn.execute(
                "INSERT INTO playlist_items (playlist_id, item_type, book_hash_id, "
                "dir_path, position, added_at) VALUES (?, 'directory', NULL, ?, ?, ?)",
                (playlist_id, path, position, now),
            )
            position += 1

        # Then books, ordered by historical save order.
        books = conn.execute(
            "SELECT hash_id FROM favorites WHERE user_id = ? ORDER BY added_date, id",
            (uid,),
        ).fetchall()
        for (hash_id,) in books:
            conn.execute(
                "INSERT INTO playlist_items (playlist_id, item_type, book_hash_id, "
                "dir_path, position, added_at) VALUES (?, 'book', ?, NULL, ?, ?)",
                (playlist_id, hash_id, position, now),
            )
            position += 1
