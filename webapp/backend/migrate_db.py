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
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()