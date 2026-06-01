"""Test scaffolding.

We spin up an in-memory SQLite (with StaticPool so the same connection is
shared across sessions), point database.engine / database.SessionLocal at it
BEFORE importing main, override the get_db dependency, and patch
email_utils._send_email_multipart so digests/updates are captured into a list
instead of going through SMTP.

The three privacy/leak tests and the two race tests live in test_feedback.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Force a unique attachment dir per test session so we don't collide with the
# production .data/feedback_attachments path.
os.environ.setdefault("FEEDBACK_ATTACHMENT_DIR", "/tmp/urantia_test_feedback_attachments")
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")


@pytest.fixture
def app_ctx(monkeypatch):
    """Yield (TestClient, helpers, captured_emails, TestSession).

    Each call gives a fresh in-memory DB and a fresh email list — tests don't
    bleed into each other.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # The in-memory test_engine doesn't inherit database.py's connect listeners,
    # so without this it would have neither FK enforcement nor the Unicode lower()
    # UDF. Register both before create_all — with StaticPool the single shared
    # connection is opened then, so the listener must already be attached.
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(test_engine, "connect")
    def _test_conn_setup(dbapi_conn, _rec):
        dbapi_conn.create_function(
            "lower", 1,
            lambda s: s.lower() if s is not None else None,
            deterministic=True,
        )
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()

    # Hot-swap database.engine BEFORE main imports / before models touch it.
    import database
    monkeypatch.setattr(database, "engine", test_engine, raising=False)
    monkeypatch.setattr(database, "SessionLocal", TestSession, raising=False)

    import models
    models.Base.metadata.create_all(bind=test_engine)

    # The in-memory test DB doesn't go through lib_schema.sql, so the
    # schema_version row that initdb.sh writes isn't there. Stamp it at
    # the current expected version so main's verify_schema_version passes.
    from sqlalchemy import text as _sql_text
    with test_engine.begin() as _c:
        _c.execute(_sql_text(
            f"INSERT OR IGNORE INTO app_meta(key,value) "
            f"VALUES ('schema_version','{database.EXPECTED_SCHEMA_VERSION}')"
        ))

    # Re-import main fresh so its _seed_admin_feedback_settings runs against
    # the in-memory DB. If already imported in the process we still need to
    # re-bind its get_db dep target and re-seed.
    if "main" in sys.modules:
        del sys.modules["main"]
    import main  # noqa: E402

    # Capture emails.
    captured: list[dict] = []
    def fake_multipart(to, subj, html, plain):
        captured.append({"to": to, "subj": subj, "html": html, "plain": plain})
    import email_utils
    monkeypatch.setattr(email_utils, "_send_email_multipart", fake_multipart)

    from fastapi.testclient import TestClient

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()
    main.app.dependency_overrides[main.get_db] = override_get_db

    from security import get_password_hash, create_access_token

    def make_user(email: str, *, admin: bool = False) -> int:
        db = TestSession()
        try:
            u = models.User(
                email=email, hashed_password=get_password_hash("x"),
                is_active=True, is_admin=admin, clearance=100 if admin else 0,
                real_name=email.split("@")[0].title(),
            )
            db.add(u); db.commit(); db.refresh(u)
            return u.id
        finally:
            db.close()

    def client_for(email: str) -> TestClient:
        c = TestClient(main.app)
        token, jti, expires_at = create_access_token({"sub": email})
        # Backend auth deps require jti to be registered in the in-memory
        # session map, so wire it up directly here.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        # Look up user_id so the row mirrors what /api/login would insert.
        db = TestSession()
        try:
            u = db.query(models.User).filter(models.User.email == email).first()
            user_id = u.id if u else 0
        finally:
            db.close()
        with main._active_sessions_lock:
            main._active_sessions[jti] = {
                "user_id": user_id,
                "email": email,
                "ip_address": "testclient",
                "user_agent": "pytest",
                "created_at": now,
                "last_seen_at": now,
                "expires_at": expires_at,
            }
        c.cookies.set("access_token", token)
        return c

    helpers = {
        "make_user": make_user,
        "client_for": client_for,
        "main": main,
        "models": models,
        "TestSession": TestSession,
    }
    yield helpers, captured, TestSession
    main.app.dependency_overrides.clear()
