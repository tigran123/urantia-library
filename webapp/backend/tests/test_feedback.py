"""Five mandatory pytest cases for the feedback subsystem.

Pin down the three privacy/leak risks (internal notes, diagnostics, directed
threads) and the two race conditions (public_id, digest throttle) called out
in IMPLEMENTATION_ORDER.md step 15.
"""
import time


def _wait_threads():
    # Daemon threads fire-and-forget the SMTP send. A short sleep is the
    # simplest barrier; everything else (writing to the DB, marking
    # digested_at) is already synchronous.
    time.sleep(0.4)


def test_feedback_throttle(app_ctx):
    """Submit several feedbacks within the 6h window → exactly one digest cycle
    of emails fires. Without the conditional UPDATE this would multiply."""
    helpers, captured, TestSession = app_ctx
    user_id = helpers["make_user"]("u@x.com")
    helpers["make_user"]("admin-a@x.com", admin=True)
    uc = helpers["client_for"]("u@x.com")
    _ = user_id

    for n in range(3):
        r = uc.post("/api/feedback", json={
            "category": "general", "subject": f"hi {n}", "body": "hello",
        })
        assert r.status_code == 200, r.text

    _wait_threads()
    digest_emails = [c for c in captured if "new feedback" in c["subj"]]
    # min_batch_size defaults to 1 → first submit clears the throttle, the
    # next two skip it (last_sent < 6h ago). Exactly one digest cycle.
    # Per-recipient partitioning means ONE email goes to ADMIN_EMAIL.
    assert len(digest_emails) == 1, [d["subj"] for d in digest_emails]
    assert digest_emails[0]["to"] == "admin@example.com"


def test_internal_note_hidden_from_user(app_ctx):
    """Privacy risk #1 + #2: admin's internal note and the diag payload must
    NOT appear in the user-side thread response."""
    helpers, captured, TestSession = app_ctx
    helpers["make_user"]("u@x.com")
    helpers["make_user"]("admin@x.com", admin=True)
    uc = helpers["client_for"]("u@x.com")
    ac = helpers["client_for"]("admin@x.com")

    r = uc.post("/api/feedback", json={
        "category": "bug", "subject": "X", "body": "Y",
        "diag": {"browser": "Firefox", "viewport": "1x1", "route": "/", "build": "b", "locale": "en"},
    })
    assert r.status_code == 200
    tid = r.json()["id"]
    pid = r.json()["public_id"]

    # Admin posts an internal note.
    r = ac.post(f"/api/feedback/{tid}/reply", json={"body": "secret note", "internal": True})
    assert r.status_code == 200

    # User fetches the thread — internal note + diag must be absent.
    r = uc.get(f"/api/feedback/{pid}")
    assert r.status_code == 200
    body = r.json()
    kinds = [m["kind"] for m in body["messages"]]
    assert "internal" not in kinds, kinds
    assert "secret note" not in " ".join(m["body"] for m in body["messages"])
    assert body["diag"] is None, body["diag"]

    # Admin side: both visible.
    r = ac.get(f"/api/feedback/{pid}")
    d = r.json()
    assert any(m["kind"] == "internal" for m in d["messages"])
    assert d["diag"] is not None
    assert d["diag"]["browser"] == "Firefox"


def test_force_digest_resets_throttle(app_ctx):
    """Force-send rolls the throttle row's timestamp forward."""
    helpers, captured, TestSession = app_ctx
    helpers["make_user"]("u@x.com")
    helpers["make_user"]("admin@x.com", admin=True)
    uc = helpers["client_for"]("u@x.com")
    ac = helpers["client_for"]("admin@x.com")
    main = helpers["main"]
    models = helpers["models"]

    # Submit one feedback so we have something pending.
    r = uc.post("/api/feedback", json={"category": "general", "subject": "S", "body": "B"})
    assert r.status_code == 200
    _wait_threads()

    # Reset digested_at to None and stash the throttle row.
    db = TestSession()
    for t in db.query(models.FeedbackThread).all():
        t.digested_at = None
    # Roll throttle back so force-digest is a clear before/after.
    db.query(models.AppMeta).filter(models.AppMeta.key == main._FEEDBACK_DIGEST_META_KEY).update(
        {models.AppMeta.value: "2020-01-01T00:00:00Z"}, synchronize_session=False,
    )
    db.commit()
    stashed_before = db.query(models.AppMeta.value).filter(
        models.AppMeta.key == main._FEEDBACK_DIGEST_META_KEY
    ).scalar()
    db.close()
    assert stashed_before == "2020-01-01T00:00:00Z"

    r = ac.post("/api/admin/feedback/digest/force")
    assert r.status_code == 200, r.text

    db = TestSession()
    after = db.query(models.AppMeta.value).filter(
        models.AppMeta.key == main._FEEDBACK_DIGEST_META_KEY
    ).scalar()
    db.close()
    assert after and after > "2020-01-01T00:00:00Z", f"throttle did not roll forward: {after}"


def test_recipients_ignored_for_non_admin(app_ctx):
    """Privacy risk #5 / #6 (server gate): a non-admin posting
    recipient_admin_ids=[...] still produces a broadcast thread."""
    helpers, captured, TestSession = app_ctx
    helpers["make_user"]("u@x.com")
    adm_id = helpers["make_user"]("admin@x.com", admin=True)
    uc = helpers["client_for"]("u@x.com")
    models = helpers["models"]

    r = uc.post("/api/feedback", json={
        "category": "general", "subject": "sneaky", "body": "try to direct",
        "recipient_admin_ids": [adm_id],
    })
    assert r.status_code == 200
    tid = r.json()["id"]
    db = TestSession()
    n = db.query(models.FeedbackRecipient).filter_by(thread_id=tid).count()
    db.close()
    assert n == 0, f"non-admin sender produced {n} recipient rows; must be 0"


def test_directed_thread_invisible_to_other_admins(app_ctx):
    """Privacy risk #3 + per-recipient digest distribution.

    Admin A directs a thread at admin B. Admin C (neither sender nor
    recipient) must not see it via any non-mine filter, and the per-recipient
    digest fires only for B."""
    helpers, captured, TestSession = app_ctx
    helpers["make_user"]("admin-a@x.com", admin=True)
    helpers["make_user"]("admin-b@x.com", admin=True)
    helpers["make_user"]("admin-c@x.com", admin=True)
    ac = helpers["client_for"]("admin-a@x.com")
    bc = helpers["client_for"]("admin-b@x.com")
    cc = helpers["client_for"]("admin-c@x.com")
    models = helpers["models"]

    # Get B's id from the API.
    r = ac.get("/api/admins")
    by_email = {a["email"]: a["id"] for a in r.json()["items"]}
    b_id = by_email["admin-b@x.com"]
    c_id = by_email["admin-c@x.com"]
    _ = c_id

    # A directs feedback at B.
    captured.clear()
    r = ac.post("/api/feedback", json={
        "category": "general", "subject": "for B only", "body": "note",
        "recipient_admin_ids": [b_id],
    })
    assert r.status_code == 200, r.text
    pid = r.json()["public_id"]

    # C lists status=new — must not see this thread.
    r = cc.get("/api/admin/feedback?status=new")
    pids = {it["public_id"] for it in r.json()["items"]}
    assert pid not in pids, f"directed thread leaked to admin C: {pids}"

    # B sees it.
    r = bc.get("/api/admin/feedback?status=new")
    pids = {it["public_id"] for it in r.json()["items"]}
    assert pid in pids, "directed thread not visible to its recipient"

    # A (the sender) does NOT see it in non-mine filter (per spec).
    r = ac.get("/api/admin/feedback?status=new")
    pids = {it["public_id"] for it in r.json()["items"]}
    assert pid not in pids, "directed thread leaked to its sender"

    # B sees it under 'mine'.
    r = bc.get("/api/admin/feedback?status=mine")
    pids = {it["public_id"] for it in r.json()["items"]}
    assert pid in pids

    # Per-recipient digest: only admin-b@x.com gets the digest email for this
    # thread.
    _wait_threads()
    by_recipient: dict[str, list[dict]] = {}
    for c in captured:
        by_recipient.setdefault(c["to"], []).append(c)
    # Admin C must not be in the digest recipient list.
    assert "admin-c@x.com" not in by_recipient, by_recipient.keys()
    # Admin B IS in the digest recipient list.
    assert "admin-b@x.com" in by_recipient, by_recipient.keys()
    # And the broadcast ADMIN_EMAIL is NOT included for a directed thread.
    assert "admin@example.com" not in by_recipient, by_recipient.keys()


def test_delete_thread_removes_messages(app_ctx):
    """Deleting a feedback thread must also delete its messages/recipients —
    this SQLite connection runs with FK enforcement off, so the endpoint cleans
    children explicitly rather than relying on ondelete=CASCADE (which would
    leave orphaned feedback_messages and an empty 'My feedback' list)."""
    helpers, _captured, TestSession = app_ctx
    helpers["make_user"]("u@x.com")
    helpers["make_user"]("admin@x.com", admin=True)
    uc = helpers["client_for"]("u@x.com")
    ac = helpers["client_for"]("admin@x.com")
    models = helpers["models"]

    r = uc.post("/api/feedback", json={"category": "general", "subject": "S", "body": "B"})
    assert r.status_code == 200
    tid = r.json()["id"]
    assert ac.post(f"/api/feedback/{tid}/reply", json={"body": "reply"}).status_code == 200

    db = TestSession()
    try:
        assert db.query(models.FeedbackMessage).filter_by(thread_id=tid).count() >= 2
    finally:
        db.close()

    assert ac.delete(f"/api/admin/feedback/{tid}").status_code == 200

    db = TestSession()
    try:
        assert db.query(models.FeedbackThread).filter_by(id=tid).first() is None
        assert db.query(models.FeedbackMessage).filter_by(thread_id=tid).count() == 0
        assert db.query(models.FeedbackRecipient).filter_by(thread_id=tid).count() == 0
    finally:
        db.close()
