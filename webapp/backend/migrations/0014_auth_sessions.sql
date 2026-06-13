-- Persist JWT sessions across backend restarts.
--
-- The auth allowlist used to live only in main.py's in-memory _active_sessions
-- dict, so every uvicorn restart wiped it and bounced all logged-in users to
-- /login. This table mirrors that dict; main._load_active_sessions() rehydrates
-- it on startup. The in-memory dict stays the runtime source of truth — rows
-- are written on login and removed on logout / admin termination.
--
-- Idempotent: create_all() runs before the startup version gate, so a first
-- restart can pre-create this table; IF NOT EXISTS makes the re-run a no-op.
CREATE TABLE IF NOT EXISTS auth_sessions (
    jti          TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    ip_address   TEXT,
    user_agent   TEXT,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user    ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires ON auth_sessions(expires_at);
