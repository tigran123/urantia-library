"""Partial unique index enforcing one Bookshelf per user.

Closes a tiny check-then-insert race in `_get_or_create_bookshelf`: two
concurrent first-time requests could both see "no shelf" and both INSERT.
With this index the loser raises IntegrityError and re-queries.
"""

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    # Idempotent (CREATE IF NOT EXISTS handles a partial first restart where
    # SQLAlchemy create_all already ran with an updated models.py).
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_playlists_one_bookshelf "
        "ON playlists(owner_id) WHERE kind = 'bookshelf'"
    )
