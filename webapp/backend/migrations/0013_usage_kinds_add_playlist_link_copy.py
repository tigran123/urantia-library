"""Add the playlist_link_copy usage-event kind to the persisted enabled-kinds set.

Follow-up to 0012. The "Copy link" button in the Share dialog now pings
POST /api/playlists/{id}/share-link-copied, which records a `playlist_link_copy`
event. `_USAGE_KINDS_ALL` already lists it, but installs that ran 0012 (or ever
saved a custom kinds set) hold a persisted allowlist without it — and 0012 is
already applied there, so it can't be re-extended. `_load_enabled_kinds()`
intersects the persisted list with the vocabulary, so without this merge every
`_record_usage_event(..., 'playlist_link_copy', ...)` short-circuits at the
kind-gate and the click never reaches Admin → Usage → Timeline.

This migration merges the one new kind in-place. Idempotent (set union). On a
fresh install the row doesn't exist yet, so this is a no-op and
`_load_enabled_kinds` falls back to "all enabled" on first startup.
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

    needed = {"playlist_link_copy"}
    if needed.issubset(vals):
        return                                  # already present — idempotent re-run

    merged = sorted(set(vals) | needed)
    conn.execute(
        "UPDATE app_meta SET value = ? WHERE key = 'usage_events_enabled_kinds'",
        (json.dumps(merged),),
    )
