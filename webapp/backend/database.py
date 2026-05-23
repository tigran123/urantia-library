import os

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

_BOOKS_DIR = os.environ.get("BOOKS_DIR", "/Books")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{_BOOKS_DIR}/.data/db/lib.db"

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
