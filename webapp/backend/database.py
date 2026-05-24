import os
import sys

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

_BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{_BOOKS_DIR}/.data/db/lib.db"

# Bump this in the same commit that adds a migrations/NNNN_* file. The
# backend's startup check (verify_schema_version) reads app_meta.schema_version
# from the DB and refuses to start when the two don't match — so a git pull
# without a corresponding `python migrate.py` on prod fails loudly instead of
# silently serving 500s from missing columns.
EXPECTED_SCHEMA_VERSION = 1

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)


@event.listens_for(engine, "connect")
def _register_unicode_lower(dbapi_conn, _connection_record):
    """SQLite's built-in lower() only folds ASCII A-Z, so case-insensitive
    search via func.lower() silently fails for Cyrillic and other non-ASCII
    text. Override it with Python's Unicode-aware str.lower()."""
    dbapi_conn.create_function(
        "lower", 1,
        lambda s: s.lower() if s is not None else None,
        deterministic=True,
    )


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_schema_version(engine_) -> None:
    """Refuse to start when the DB's schema_version doesn't match
    EXPECTED_SCHEMA_VERSION. Called once from main.py at boot."""
    with engine_.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM app_meta WHERE key='schema_version'")
        ).fetchone()
    if row is None:
        print(
            "FATAL: app_meta.schema_version is missing. Stamp the baseline with:\n"
            f"  sqlite3 {_BOOKS_DIR}/.data/db/lib.db "
            "\"INSERT OR IGNORE INTO app_meta(key,value) VALUES ('schema_version','1');\"",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        actual = int(row[0])
    except (TypeError, ValueError):
        print(
            f"FATAL: app_meta.schema_version is not an integer: {row[0]!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    if actual != EXPECTED_SCHEMA_VERSION:
        print(
            f"FATAL: DB schema_version is {actual}, code expects "
            f"{EXPECTED_SCHEMA_VERSION}. Run `python migrate.py` to apply "
            f"pending migrations.",
            file=sys.stderr,
        )
        sys.exit(1)
