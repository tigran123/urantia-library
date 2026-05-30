"""Add the playlist usage-event kinds to the persisted enabled-kinds set.

Same situation as migration 0005: main.py's `_USAGE_KINDS_ALL` grew five new
entries (playlist_create / playlist_delete / playlist_add_item /
playlist_remove_item / playlist_visibility), but an already-deployed install
that has saved a kinds set in `app_meta.usage_events_enabled_kinds` still holds
the OLD vocabulary. `_load_enabled_kinds()` intersects the persisted list with
the current vocabulary, so the new kinds would be silently absent and every
`_record_usage_event(..., 'playlist_*', ...)` would short-circuit at the
kind-gate — the playlist actions would never reach Admin → Usage → Timeline.

This migration merges the five new kinds in-place. Idempotent (set union). On a
fresh install the row doesn't exist yet, so this is a no-op and
`_load_enabled_kinds` falls back to "all enabled" on first startup. The admin
can still disable any kind afterwards via PUT /api/admin/usage/settings.
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

    needed = {
        "playlist_create", "playlist_delete",
        "playlist_add_item", "playlist_remove_item",
        "playlist_visibility",
    }
    if needed.issubset(vals):
        return                                  # already present — idempotent re-run

    merged = sorted(set(vals) | needed)
    conn.execute(
        "UPDATE app_meta SET value = ? WHERE key = 'usage_events_enabled_kinds'",
        (json.dumps(merged),),
    )
