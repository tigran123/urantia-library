-- Recommendations: admins (and, in a future iteration, trusted users) mark
-- books they want to surface as picks. The actual symlink under
-- /Books/Recommended/ is what drives discovery (it appears in
-- book_locations like any other location), and this table records who flipped
-- the flag and when. One row per recommended book; deleting the row when an
-- admin un-recommends keeps the table sparse.
CREATE TABLE book_recommendations (
    hash_id        TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    recommended_by INTEGER NOT NULL REFERENCES users(id),
    recommended_at TEXT NOT NULL                                -- ISO-8601 UTC
);
CREATE INDEX ix_book_recommendations_at ON book_recommendations(recommended_at DESC);
