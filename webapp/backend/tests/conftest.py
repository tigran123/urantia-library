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

    # Hot-swap database.engine BEFORE main imports / before models touch it.
    import database
    monkeypatch.setattr(database, "engine", test_engine, raising=False)
    monkeypatch.setattr(database, "SessionLocal", TestSession, raising=False)

    import models
    models.Base.metadata.create_all(bind=test_engine)

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
        c.cookies.set("access_token", create_access_token({"sub": email}))
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
