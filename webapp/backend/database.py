from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./auth.db"

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


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
