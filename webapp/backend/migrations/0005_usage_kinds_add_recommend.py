"""Add 'recommend' and 'unrecommend' to the persisted usage_events_enabled_kinds.

Background: when the book.recommend feature shipped (migration 0003), main.py's
`_USAGE_KINDS_ALL` tuple grew two new entries — but the persisted
`app_meta.usage_events_enabled_kinds` row on already-deployed installs still
held the OLD vocabulary. `_load_enabled_kinds()` filters the loaded list
against the current `_USAGE_KINDS_ALL` (an intersection), so the two new
kinds are silently absent from `_enabled_kinds` after startup. Every
`_record_usage_event(..., 'recommend', ...)` call short-circuits at the
kind-gate. Admin → Usage Timeline and per-user Activity therefore never
surface recommend/unrecommend events on those installs.

This migration adds the two new kinds in-place. Idempotent re-application is
safe (the merge is a set union). On fresh installs the row doesn't exist
yet — the upgrade is a no-op and `_load_enabled_kinds` falls back to
"all enabled" on first startup.

After this migration, the admin can still disable either kind explicitly via
PUT /api/admin/usage/settings.
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

    needed = {"recommend", "unrecommend"}
    if needed.issubset(vals):
        return                                  # already present — idempotent re-run

    merged = sorted(set(vals) | needed)
    conn.execute(
        "UPDATE app_meta SET value = ? WHERE key = 'usage_events_enabled_kinds'",
        (json.dumps(merged),),
    )
