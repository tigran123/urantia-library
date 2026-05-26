#!/bin/bash
# Delete usage_events older than 24 months. Driven by
# webapp/urantia-library-prune.timer (daily 04:00 UTC, after the 03:00 backup).
#
# Why a separate script vs. ad-hoc cron: this keeps the retention policy as a
# single auditable artifact, with the same systemd-unit conventions as
# nightly_backup.sh. A reviewer asking "what is the retention?" can answer it
# from this file alone.
#
# The Privacy Policy promises 24 months. If you ever change the policy text,
# change this clause to match — and vice-versa.

set -euo pipefail

DB="${LIB_DB:-/Books/.data/db/lib.db}"
KEEP_MONTHS="${KEEP_MONTHS:-24}"

if [[ ! -f "$DB" ]]; then
    echo "prune_usage_events: source DB not found: $DB" >&2
    exit 1
fi

before=$(sqlite3 "$DB" "SELECT count(*) FROM usage_events")
deleted=$(sqlite3 "$DB" "
    DELETE FROM usage_events
     WHERE ts < strftime('%Y-%m-%dT%H:%M:%SZ', datetime('now', '-${KEEP_MONTHS} months'));
    SELECT changes();
")
after=$(sqlite3 "$DB" "SELECT count(*) FROM usage_events")

echo "prune_usage_events: deleted ${deleted} row(s) older than ${KEEP_MONTHS} months (${before} -> ${after})."
