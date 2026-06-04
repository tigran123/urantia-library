-- Index books.import_date. It already backs library_stats' "added this week"
-- count, and now also backs the /api/search `sort=import_date` ORDER BY and the
-- `added:<window>` range filter. Mirrors idx_books_size. IF NOT EXISTS so a
-- re-applied migration (manual rollback re-test, restore-from-backup) is a no-op.
CREATE INDEX IF NOT EXISTS idx_books_import_date ON books(import_date);
