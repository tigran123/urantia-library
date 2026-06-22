-- One-time, expiring password-reset tokens (the "Forgot your password?" flow).
--
-- Issued by POST /api/forgot-password and consumed once by POST /api/reset-password
-- (which stamps used_at). Only sha256(token) is stored (token_hash), so a DB read
-- can't reconstruct a live reset link — the plaintext token exists only in the
-- emailed link. user_id ON DELETE CASCADE reaps a deleted user's tokens;
-- _purge_expired_reset_tokens() sweeps expired/used rows at startup, mirroring
-- auth_sessions.
--
-- Idempotent: create_all() runs before the startup version gate, so a first
-- restart can pre-create this table; IF NOT EXISTS makes the re-run a no-op.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,                  -- sha256(token) hex, 64 chars
    created_at  TEXT NOT NULL,                  -- ISO-8601 UTC
    expires_at  TEXT NOT NULL,                  -- ISO-8601 UTC
    used_at     TEXT                            -- ISO-8601 UTC; NULL = unused
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_password_reset_tokens_hash    ON password_reset_tokens(token_hash);
CREATE INDEX        IF NOT EXISTS ix_password_reset_tokens_user    ON password_reset_tokens(user_id);
CREATE INDEX        IF NOT EXISTS ix_password_reset_tokens_expires ON password_reset_tokens(expires_at);
