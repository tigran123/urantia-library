"""Foundation module (extracted from main.py): process-global in-memory state
shared across the app — the presence map, the active-session map, the staging
map, the integrity-job registry, and the usage-event kill-switch set. Leaf of
the foundation DAG.

NOTE: `_enabled_kinds` is REASSIGNED at runtime by
`background._load_enabled_kinds()`. Any reader MUST reference it as
`state._enabled_kinds` (attribute access), never bind the name at import time,
so it always sees the latest value after an admin saves the settings.

`_USAGE_KINDS_ALL` lives in `config` (a pure leaf that imports nothing from the
split set), so `import config` here for the initial value introduces no cycle
and preserves main.py's original initial value exactly."""
import threading
from datetime import datetime, timezone
from config import _USAGE_KINDS_ALL

_last_seen: dict[int, datetime] = {}
_last_seen_lock = threading.Lock()

# Active JWT sessions, keyed by the token's `jti` claim. A request whose jti
# is not here is treated as terminated (401), even if the JWT signature and
# `exp` are still valid. This dict is the runtime source of truth (revocation
# gate, Admin → Sessions panel, online counts) but is now write-through mirrored
# to the `auth_sessions` table: login adds a row, logout / admin termination
# remove it, and `_load_active_sessions()` rehydrates this dict from the table
# on startup. So a backend restart no longer logs everyone out — to invalidate
# every session at once, rotate `JWT_SECRET_KEY` (every JWT then fails its
# signature check).
_active_sessions: dict[str, dict] = {}
_active_sessions_lock = threading.Lock()

def _purge_expired_sessions_locked() -> None:
    """Drop entries whose `expires_at` is in the past. Caller holds the lock."""
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _active_sessions.items() if v["expires_at"] <= now]
    for k in expired:
        _active_sessions.pop(k, None)

# batch_id -> (audit_row_id, last_touch_ts). In-memory, like _STAGING: a restart
# mid-batch just starts a fresh audit row for the remaining volumes (rare, and
# the batch's staging is gone too). Sequential per-batch commits (the client
# loops) mean no concurrent append to a row.
_AUDIT_BATCHES: dict[str, tuple[int, float]] = {}
_AUDIT_BATCHES_LOCK = threading.Lock()
_AUDIT_BATCH_TTL_S = 3600

_STAGING: dict[str, dict] = {}
_STAGING_LOCK = threading.Lock()

INTEGRITY_JOBS: dict[str, dict] = {}
INTEGRITY_JOBS_LOCK = threading.Lock()
INTEGRITY_JOB_TTL_SECONDS = 3600
INTEGRITY_ALL_RESULTS_CAP = 5000

_enabled_kinds: frozenset[str] = frozenset(_USAGE_KINDS_ALL)
