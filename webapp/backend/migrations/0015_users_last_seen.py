"""Add users.last_seen_at — a persisted per-user "last seen" timestamp so the
Admin → Users panel can show last activity for *all* users, including those with
no live session (the Sessions panel only ever lists live sessions).

The hot auth deps (get_current_user / get_optional_user) still do no per-request
DB writes; a periodic background flush in main.py mirrors the in-memory
_last_seen map into this column (with a final flush on clean shutdown).

Idempotent:
- ALTER TABLE ADD COLUMN is guarded on PRAGMA table_info, because create_all()
  on a fresh DB (or a re-run of this migration) would otherwise error.
- The one-time backfill seeds each existing user from the best historical signal
  we have — the latest of their auth_sessions.last_seen_at and usage_events.ts —
  and only touches rows still NULL, so a re-run (or the live flush having already
  written a value) is never clobbered. Users with no history stay NULL ("Never").
"""
import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "last_seen_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT")  # ISO-8601 UTC

    # Best-effort backfill: greatest known activity per user across the two
    # tables that already record timestamps. MAX() ignores NULLs and returns
    # NULL when a user has neither, so never-active users remain NULL.
    conn.execute(
        """
        UPDATE users
           SET last_seen_at = (
               SELECT MAX(t) FROM (
                   SELECT MAX(last_seen_at) AS t FROM auth_sessions WHERE user_id = users.id
                   UNION ALL
                   SELECT MAX(ts)           AS t FROM usage_events  WHERE user_id = users.id
               )
           )
         WHERE last_seen_at IS NULL
        """
    )
