"""Add the playlist_share / playlist_copy usage-event kinds to the persisted
enabled-kinds set.

Same situation as migrations 0005 and 0007: main.py's `_USAGE_KINDS_ALL` grew
two new entries (playlist_share / playlist_copy), but an already-deployed install
that has saved a kinds set in `app_meta.usage_events_enabled_kinds` still holds
the OLD vocabulary. `_load_enabled_kinds()` intersects the persisted list with
the current vocabulary, so the new kinds would be silently absent and every
`_record_usage_event(..., 'playlist_share'|'playlist_copy', ...)` would
short-circuit at the kind-gate — sharing and copying a playlist would never
reach Admin → Usage → Timeline.

This migration merges the two new kinds in-place. Idempotent (set union). On a
fresh install the row doesn't exist yet, so this is a no-op and
`_load_enabled_kinds` falls back to "all enabled" on first startup. The admin
can still disable any kind afterwards via PUT /api/admin/usage/settings.

(playlist_link_copy was added later in migration 0013 — once 0012 had been
applied somewhere its kind set could no longer be extended in place.)
"""
import json
import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "SELECT value FROM app_meta WHERE key='usage_events_enabled_kinds'"
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return                                  # fresh install — nothing persisted yet

    try:
        vals = json.loads(row[0])
    except (ValueError, json.JSONDecodeError):
        return                                  # malformed — _load_enabled_kinds also falls back to all-enabled

    if not isinstance(vals, list):
        return

    needed = {"playlist_share", "playlist_copy"}
    if needed.issubset(vals):
        return                                  # already present — idempotent re-run

    merged = sorted(set(vals) | needed)
    conn.execute(
        "UPDATE app_meta SET value = ? WHERE key = 'usage_events_enabled_kinds'",
        (json.dumps(merged),),
    )
