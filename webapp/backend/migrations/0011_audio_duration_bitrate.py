"""Add books.duration and books.bitrate (audio/video media metadata).

The Album view needs each audio track's length and bitrate. These used to be
probed client-side (each browser fetching every file's metadata, which also gave
any client a `?probe=1` flag to suppress usage logging — see the M2 change). They
are now derived server-side with ffprobe at import and exposed in the browse
payload, so the columns must exist on already-deployed databases too.

`duration` is REAL (seconds), `bitrate` is INTEGER (bits/sec); both NULL until
populated at import or by backfill_durations.py. lib_schema.sql carries them for
fresh installs.

Idempotent: ALTER TABLE ADD COLUMN is not idempotent on its own, and
create_all() only creates missing *tables*, never missing columns — so guard each
ADD on PRAGMA table_info so a re-run (or a first restart that already ran
create_all on a fresh DB) is a no-op.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(books)")}
    if "duration" not in existing:
        conn.execute("ALTER TABLE books ADD COLUMN duration REAL")
    if "bitrate" not in existing:
        conn.execute("ALTER TABLE books ADD COLUMN bitrate INTEGER")
