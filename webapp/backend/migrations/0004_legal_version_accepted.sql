-- Track which LEGAL_VERSION each user (and pending registration request) most
-- recently accepted. NULL = legacy row; treated as "not current" so the holder
-- is re-prompted on next /api/me. Acceptance preserves accepted_legal_at as
-- the *latest* acceptance timestamp; old timestamps are not nulled.
ALTER TABLE users                 ADD COLUMN legal_version_accepted TEXT;
ALTER TABLE registration_requests ADD COLUMN legal_version_accepted TEXT;

-- Backfill: everyone who accepted SOMETHING accepted the previous version
-- (the .md files' "Last updated: 26 May 2026" stamp). LEGAL_VERSION lands in
-- code as '2026-05-29' in the same commit, so all existing users will see the
-- re-prompt modal on their next /api/me poll. The window between migration
-- and the backend pickup is seconds; we tolerate the chance that a fresh
-- registration slips through with the new value (UPDATE only touches rows
-- whose accepted_legal_at was already non-NULL).
UPDATE users
   SET legal_version_accepted = '2026-05-26'
 WHERE accepted_legal_at IS NOT NULL;
UPDATE registration_requests
   SET legal_version_accepted = '2026-05-26'
 WHERE accepted_legal_at IS NOT NULL;

-- Seed the email-blast throttle key to the CURRENT LEGAL_VERSION so the
-- rollout itself doesn't spam everyone. The first real blast fires only on
-- the NEXT bump (when the operator next edits a .md and advances
-- LEGAL_VERSION in database.py). See _maybe_send_legal_blast in main.py.
--
-- OR IGNORE (not OR REPLACE) so a manual re-application — schema_version
-- hand-rolled back for re-testing, restore-from-backup that already had a
-- newer throttle — doesn't clobber a live value and re-fire the blast.
-- (`_maybe_send_legal_blast` itself also self-seeds via INSERT OR IGNORE,
-- so the fresh-install path that runs lib_schema.sql alone is covered.)
INSERT OR IGNORE INTO app_meta(key, value) VALUES ('legal_blast_version', '2026-05-29');
