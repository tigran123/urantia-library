import sqlite3
import os
import sys

def migrate():
    db_path = os.path.join(os.path.dirname(__file__), "auth.db")
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}")
        sys.exit(1)
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if column already exists to make script idempotent
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]

        if "avatar_url" in columns:
            print("Migration already applied: 'avatar_url' column exists in 'users' table.")
        else:
            cursor.execute("ALTER TABLE users ADD COLUMN avatar_url VARCHAR;")
            conn.commit()
            print("Migration successful: Added 'avatar_url' to 'users' table.")

        # directory_favorites — added when directory bookmarking came back.
        # On dev machines where the SQLAlchemy create_all ran before this
        # migration, the table exists but in a minimal form (no AUTOINCREMENT,
        # no added_date, no ON DELETE on the FK). Detect that and recreate.
        CANONICAL_DDL = """
            CREATE TABLE directory_favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                path VARCHAR NOT NULL,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, path)
            );
        """
        CANONICAL_INDEX = (
            "CREATE INDEX idx_directory_favorites_user ON directory_favorites(user_id);"
        )

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='directory_favorites'"
        )
        exists = cursor.fetchone() is not None
        if not exists:
            cursor.execute(CANONICAL_DDL)
            cursor.execute(CANONICAL_INDEX)
            conn.commit()
            print("Migration successful: Created 'directory_favorites' table.")
        else:
            cursor.execute("PRAGMA table_info(directory_favorites)")
            cols = {row[1] for row in cursor.fetchall()}
            if "added_date" in cols:
                print("Migration already applied: 'directory_favorites' table is canonical.")
            else:
                # SQLite can't ALTER columns or change PK behavior in place;
                # do the standard recreate-and-copy dance to preserve any rows.
                cursor.execute("ALTER TABLE directory_favorites RENAME TO _df_old;")
                cursor.execute(CANONICAL_DDL)
                cursor.execute(CANONICAL_INDEX)
                cursor.execute(
                    "INSERT INTO directory_favorites (id, user_id, path) "
                    "SELECT id, user_id, path FROM _df_old;"
                )
                cursor.execute("DROP TABLE _df_old;")
                conn.commit()
                print("Migration successful: Rebuilt 'directory_favorites' to canonical schema.")
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()