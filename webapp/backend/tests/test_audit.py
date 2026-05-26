"""Smoke tests for the librarian audit log.

These don't try to cover every call site — instead they pick three
representative paths (book edit, clearance change, comment moderation),
confirm that an audit row lands with the right action / actor / target,
and that the helper's transaction-riding semantic actually holds
(no row if the surrounding commit doesn't happen).
"""
from __future__ import annotations


def test_book_edit_writes_audit_row(app_ctx):
    helpers, _emails, TestSession = app_ctx
    models = helpers["models"]
    main   = helpers["main"]

    admin_id = helpers["make_user"]("alice@example.com", admin=True)
    db = TestSession()
    try:
        db.add(models.Book(
            id="hash_for_book_edit", title="Old Title", original_filename="x.pdf",
            import_date="2026-05-26T00:00:00Z", clearance=50,
        ))
        db.commit()
    finally:
        db.close()

    c = helpers["client_for"]("alice@example.com")
    r = c.put("/api/admin/books/hash_for_book_edit",
              json={"title": "New Title", "author": "Some Author"})
    assert r.status_code == 200, r.text

    db = TestSession()
    try:
        rows = db.query(models.AdminAuditLog).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.actor_user_id == admin_id
        assert row.action == "book.edit"
        assert row.target_kind == "book"
        assert row.target_id == "hash_for_book_edit"
        assert "title" in row.summary  # contains the changed-field list
        import json
        details = json.loads(row.details_json)
        assert details["changed"]["title"] == ["Old Title", "New Title"]
        assert details["changed"]["author"] == [None, "Some Author"]
    finally:
        db.close()


def test_bulk_clearance_no_row_when_unchanged(app_ctx):
    """The bulk variant must also skip the audit when no book actually changes
    — SQLite's UPDATE returns rows-matched not rows-changed, so re-applying the
    same clearance would naively look like a mass change. Regression guard
    for the fix added during code review."""
    helpers, _emails, TestSession = app_ctx
    models = helpers["models"]

    helpers["make_user"]("alice@example.com", admin=True)
    db = TestSession()
    try:
        for i in range(3):
            db.add(models.Book(
                id=f"hash_bulk_{i}", title=f"T{i}", original_filename="x.pdf",
                import_date="2026-05-26T00:00:00Z", clearance=10,
            ))
        db.commit()
    finally:
        db.close()

    c = helpers["client_for"]("alice@example.com")
    r = c.post("/api/admin/books/clearance",
               json={"hash_ids": ["hash_bulk_0", "hash_bulk_1", "hash_bulk_2"],
                     "clearance": 10})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 0

    db = TestSession()
    try:
        assert db.query(models.AdminAuditLog).count() == 0
    finally:
        db.close()


def test_book_clearance_no_row_when_unchanged(app_ctx):
    """If the new value equals the existing one, _audit() is not called and no
    row should be written. Confirms the call-site diff guards."""
    helpers, _emails, TestSession = app_ctx
    models = helpers["models"]

    helpers["make_user"]("alice@example.com", admin=True)
    db = TestSession()
    try:
        db.add(models.Book(
            id="hash_for_unchanged", title="T", original_filename="x.pdf",
            import_date="2026-05-26T00:00:00Z", clearance=42,
        ))
        db.commit()
    finally:
        db.close()

    c = helpers["client_for"]("alice@example.com")
    r = c.put("/api/admin/books/hash_for_unchanged/clearance", json={"clearance": 42})
    assert r.status_code == 200

    db = TestSession()
    try:
        assert db.query(models.AdminAuditLog).count() == 0
    finally:
        db.close()


def test_audit_feed_requires_admin(app_ctx):
    """A signed-in non-admin gets 403; an anonymous request gets 401."""
    helpers, _emails, _ = app_ctx
    helpers["make_user"]("normal@example.com", admin=False)
    c = helpers["client_for"]("normal@example.com")
    assert c.get("/api/admin/audit").status_code == 403
    assert c.get("/api/admin/audit/stats").status_code == 403

    from fastapi.testclient import TestClient
    anon = TestClient(helpers["main"].app)
    assert anon.get("/api/admin/audit").status_code == 401


def test_audit_feed_pagination_and_filters(app_ctx):
    """Feed honours limit + cursor + actor/action/since filters and orders newest-first."""
    helpers, _emails, TestSession = app_ctx
    models = helpers["models"]
    main   = helpers["main"]

    alice_id = helpers["make_user"]("alice@example.com", admin=True)
    bob_id   = helpers["make_user"]("bob@example.com",   admin=True)

    # Seed 5 rows with alternating actors and actions, ascending created_at.
    db = TestSession()
    try:
        for i in range(5):
            db.add(models.AdminAuditLog(
                created_at=f"2026-05-2{i}T00:00:00Z",
                actor_user_id=alice_id if i % 2 == 0 else bob_id,
                action="book.upload" if i < 3 else "book.delete",
                target_kind="book", target_id=f"hash{i}",
                summary=f"row {i}",
            ))
        db.commit()
    finally:
        db.close()

    c = helpers["client_for"]("alice@example.com")

    # No filters — get all 5, newest first.
    body = c.get("/api/admin/audit").json()
    assert [e["summary"] for e in body["entries"]] == ["row 4", "row 3", "row 2", "row 1", "row 0"]
    assert body["next_cursor"] is None

    # Pagination: limit=2 should yield 2 entries + a cursor.
    body = c.get("/api/admin/audit?limit=2").json()
    assert len(body["entries"]) == 2
    assert body["entries"][0]["summary"] == "row 4"
    assert body["next_cursor"] is not None
    body2 = c.get(f"/api/admin/audit?limit=2&cursor={body['next_cursor']}").json()
    assert [e["summary"] for e in body2["entries"]] == ["row 2", "row 1"]

    # Filter by actor.
    body = c.get(f"/api/admin/audit?actor_user_id={bob_id}").json()
    assert all(e["actor"]["email"] == "bob@example.com" for e in body["entries"])
    assert len(body["entries"]) == 2  # rows 1 and 3

    # Filter by action.
    body = c.get("/api/admin/audit?action=book.delete").json()
    assert all(e["action"] == "book.delete" for e in body["entries"])
    assert len(body["entries"]) == 2  # rows 3 and 4

    # Filter by since (strictly inclusive on created_at).
    body = c.get("/api/admin/audit?since=2026-05-23T00:00:00Z").json()
    assert [e["summary"] for e in body["entries"]] == ["row 4", "row 3"]


def test_audit_stats_leaderboard(app_ctx):
    """Stats groups by actor+action, sorts by total desc, resolves user info."""
    helpers, _emails, TestSession = app_ctx
    models = helpers["models"]

    alice_id = helpers["make_user"]("alice@example.com", admin=True)
    bob_id   = helpers["make_user"]("bob@example.com",   admin=True)

    db = TestSession()
    try:
        # Alice: 3 uploads, 1 edit; Bob: 1 upload.
        for action, actor_id, n in [("book.upload", alice_id, 3),
                                    ("book.edit",   alice_id, 1),
                                    ("book.upload", bob_id,   1)]:
            for _ in range(n):
                db.add(models.AdminAuditLog(
                    created_at="2026-05-26T00:00:00Z",
                    actor_user_id=actor_id,
                    action=action,
                    summary="x",
                ))
        db.commit()
    finally:
        db.close()

    c = helpers["client_for"]("alice@example.com")
    body = c.get("/api/admin/audit/stats?since=2026-01-01T00:00:00Z").json()

    leaders = body["leaders"]
    assert len(leaders) == 2
    # Alice has 4 total, Bob has 1 — sorted desc.
    assert leaders[0]["actor"]["email"] == "alice@example.com"
    assert leaders[0]["total"] == 4
    assert leaders[0]["totals"] == {"book.upload": 3, "book.edit": 1}
    assert leaders[1]["actor"]["email"] == "bob@example.com"
    assert leaders[1]["total"] == 1


def test_comment_moderate_records_decision(app_ctx):
    """Approve + delete each produce a row with the right decision tag."""
    helpers, _emails, TestSession = app_ctx
    models = helpers["models"]

    helpers["make_user"]("alice@example.com", admin=True)
    author_id = helpers["make_user"]("bob@example.com", admin=False)
    db = TestSession()
    try:
        db.add(models.Book(
            id="hash_for_comment", title="T", original_filename="x.pdf",
            import_date="2026-05-26T00:00:00Z", clearance=0,
        ))
        db.add(models.BookComment(
            id=1, user_id=author_id, hash_id="hash_for_comment",
            body="hello", status="pending",
            created_at="2026-05-26T00:00:00Z", updated_at="2026-05-26T00:00:00Z",
        ))
        db.commit()
    finally:
        db.close()

    c = helpers["client_for"]("alice@example.com")
    r = c.post("/api/admin/comments/1/approve")
    assert r.status_code == 200, r.text
    r = c.delete("/api/admin/comments/1")
    assert r.status_code == 200, r.text

    db = TestSession()
    try:
        rows = db.query(models.AdminAuditLog).order_by(models.AdminAuditLog.id).all()
        assert len(rows) == 2
        assert rows[0].action == "comment.moderate"
        assert rows[1].action == "comment.moderate"
        import json
        assert json.loads(rows[0].details_json)["decision"] == "approve"
        assert json.loads(rows[1].details_json)["decision"] == "delete"
        # The summary should mention the comment author's email.
        assert "bob@example.com" in rows[0].summary
        assert "bob@example.com" in rows[1].summary
    finally:
        db.close()
