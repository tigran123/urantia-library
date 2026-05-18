-- ==============================================================================
-- Urantia Library - Core Schema
-- Architecture: Hybrid Content-Addressable Storage (CAS)
-- ==============================================================================

-- 1. Users and Authentication
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email VARCHAR UNIQUE NOT NULL,
    hashed_password VARCHAR NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    clearance INTEGER NOT NULL DEFAULT 0,
    avatar_url VARCHAR,
    search_per_page INTEGER DEFAULT 50
);

CREATE TABLE registration_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, 
    email VARCHAR UNIQUE NOT NULL, 
    status VARCHAR, 
    source VARCHAR, 
    purpose VARCHAR, 
    token VARCHAR UNIQUE
);

-- ==============================================================================
-- 2. The CAS Metadata Vault
-- ==============================================================================
CREATE TABLE books (
    id VARCHAR PRIMARY KEY,             -- The BLAKE2b Hash of the physical file
    title VARCHAR,                      
    author VARCHAR,                     
    publisher VARCHAR,                  
    published VARCHAR,                  -- VARCHAR to accommodate '2003-05-15T00:00:00+00:00'
    description TEXT,                   -- Renamed from 'annotation' to match Vue UI
    tags VARCHAR,
    series VARCHAR,
    languages VARCHAR,
    identifiers VARCHAR,                -- e.g., 'isbn:5-271-05460-8'
    original_filename VARCHAR NOT NULL, -- Fallback for unparseable files
    needs_review BOOLEAN DEFAULT FALSE, -- Flag for manual librarian intervention
    clearance INTEGER NOT NULL DEFAULT 100, -- Minimum user clearance required to read; high by default so only admins see new books until classified down
    last_verified_at TEXT,              -- ISO-8601 UTC of last integrity check
    last_verified_ok BOOLEAN,           -- Result of last integrity check (NULL = never run)
    last_verified_mode VARCHAR,         -- 'quick' or 'full'
    last_verified_error VARCHAR,        -- Short failure key when last_verified_ok = FALSE
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_books_clearance ON books(clearance);

-- ==============================================================================
-- 3. The Spatial Search Index
-- ==============================================================================
CREATE TABLE book_locations (
    symlink_path VARCHAR PRIMARY KEY,   -- Primary Key prevents the SQLAlchemy 500 Error
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE
);

CREATE INDEX idx_book_locations_hash ON book_locations(hash_id);

-- ==============================================================================
-- 4. User Data (Strictly Hash-Based)
-- ==============================================================================
CREATE TABLE favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, hash_id)
);

CREATE TABLE reading_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    hash_id VARCHAR NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    location VARCHAR NOT NULL,
    UNIQUE(user_id, hash_id)
);

CREATE TABLE directory_favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    path VARCHAR NOT NULL,
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, path)
);

CREATE INDEX idx_directory_favorites_user ON directory_favorites(user_id);
