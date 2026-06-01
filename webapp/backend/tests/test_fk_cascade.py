"""FK enforcement regression tests (schema v9).

PRAGMA foreign_keys=ON is now set on every connection (database.py, and the
test engine in conftest), so the schema's ON DELETE CASCADE / SET NULL clauses
are load-bearing rather than decorative. These pin the two behaviours the v9
migration introduced:

  * deleting a book / playlist / thread / comment cascades its child rows
    (the app no longer clears them by hand — see main.py delete handlers),
  * deleting a user cascades the user's private data but DETACHES (SET NULL)
    the identity-bearing records (audit log, feedback authorship in someone
    else's thread, recommendations).

See also test_feedback.test_delete_thread_removes_messages, which now exercises
the feedback_threads -> feedback_messages CASCADE path.
"""

TS = "2026-01-01T00:00:00Z"


def _add_book(db, models, h):
    db.add(models.Book(id=h, original_filename=f"{h}.pdf", import_date=TS, clearance=0))
    # Flush the parent before any child row is added: the ORM unit-of-work orders
    # flushes by relationship() (there are none here), not by raw ForeignKey, so
    # under FK enforcement a batched child INSERT could otherwise precede the book.
    db.flush()


def test_book_delete_cascades_children(app_ctx):
    """Deleting a book cascades every per-user/per-book row and SET-NULLs the
    book link on any feedback thread (the thread itself is kept)."""
    helpers, _captured, TestSession = app_ctx
    models = helpers["models"]
    uid = helpers["make_user"]("u@x.com")
    helpers["make_user"]("admin@x.com", admin=True)
    ac = helpers["client_for"]("admin@x.com")
    h = "b" * 16  # too short to be a real vault hash, so no real file is touched

    db = TestSession()
    try:
        _add_book(db, models, h)
        db.add(models.BookLocation(hash_id=h, symlink_path="Topic/book.pdf"))
        db.add(models.Favorite(user_id=uid, hash_id=h))
        db.add(models.ReadingProgress(user_id=uid, hash_id=h, location="0"))
        db.add(models.BookRating(user_id=uid, hash_id=h, rating=5, created_at=TS, updated_at=TS))
        db.add(models.BookComment(user_id=uid, hash_id=h, body="hi", status="approved", created_at=TS, updated_at=TS))
        db.add(models.Annotation(user_id=uid, hash_id=h, anchor="{}", selected_text="x",
                                 is_public=False, status="approved", created_at=TS, updated_at=TS))
        db.add(models.BookRecommendation(hash_id=h, recommended_by=uid, recommended_at=TS))
        db.add(models.FeedbackThread(public_id="UL-1", user_id=uid, category="book", subject="s",
                                     status="new", book_hash_id=h, created_at=TS, updated_at=TS))
        db.commit()
    finally:
        db.close()

    assert ac.delete(f"/api/admin/books/{h}").status_code == 200

    db = TestSession()
    try:
        assert db.query(models.Book).filter_by(id=h).first() is None
        assert db.query(models.BookLocation).filter_by(hash_id=h).count() == 0
        assert db.query(models.Favorite).filter_by(hash_id=h).count() == 0
        assert db.query(models.ReadingProgress).filter_by(hash_id=h).count() == 0
        assert db.query(models.BookRating).filter_by(hash_id=h).count() == 0
        assert db.query(models.BookComment).filter_by(hash_id=h).count() == 0
        assert db.query(models.Annotation).filter_by(hash_id=h).count() == 0
        assert db.query(models.BookRecommendation).filter_by(hash_id=h).count() == 0
        # Thread kept, book link detached.
        th = db.query(models.FeedbackThread).filter_by(public_id="UL-1").first()
        assert th is not None and th.book_hash_id is None
    finally:
        db.close()


def test_user_delete_cascades_private_and_detaches_identity(app_ctx):
    """Deleting a user CASCADE-removes their private data but keeps (SET NULL)
    the identity-bearing rows: a message they authored in someone else's thread,
    audit-log entries, and recommendations. There is no account-deletion
    endpoint, so this drives the DELETE directly to test the FK actions."""
    helpers, _captured, TestSession = app_ctx
    models = helpers["models"]
    uid = helpers["make_user"]("u@x.com")   # the user to delete
    vid = helpers["make_user"]("v@x.com")   # survivor who owns a shared thread
    h = "c" * 16

    db = TestSession()
    try:
        _add_book(db, models, h)
        # U's private data.
        db.add(models.Favorite(user_id=uid, hash_id=h))
        db.add(models.ReadingProgress(user_id=uid, hash_id=h, location="0"))
        db.add(models.BookRating(user_id=uid, hash_id=h, rating=4, created_at=TS, updated_at=TS))
        db.add(models.BookComment(user_id=uid, hash_id=h, body="c", status="approved", created_at=TS, updated_at=TS))
        db.add(models.Annotation(user_id=uid, hash_id=h, anchor="{}", selected_text="x",
                                 is_public=False, status="approved", created_at=TS, updated_at=TS))
        db.add(models.DirectoryFavorite(user_id=uid, path="Topic"))
        pl = models.Playlist(owner_id=uid, name="P", visibility="private", kind="normal",
                             created_at=TS, updated_at=TS)
        db.add(pl)
        db.flush()
        pl_id = pl.id
        db.add(models.PlaylistItem(playlist_id=pl_id, item_type="book", book_hash_id=h,
                                   position=0, added_at=TS))
        # U's own thread (+ message) — cascades away entirely with U.
        ut = models.FeedbackThread(public_id="UL-U", user_id=uid, category="general", subject="s",
                                   status="new", created_at=TS, updated_at=TS)
        db.add(ut)
        db.flush()
        db.add(models.FeedbackMessage(thread_id=ut.id, author_id=uid, kind="message", body="b", created_at=TS))
        # V's thread, with a message AUTHORED by U — thread survives, author detaches.
        vt = models.FeedbackThread(public_id="UL-V", user_id=vid, category="general", subject="s",
                                   status="new", created_at=TS, updated_at=TS)
        db.add(vt)
        db.flush()
        vt_id = vt.id
        um = models.FeedbackMessage(thread_id=vt_id, author_id=uid, kind="message", body="b", created_at=TS)
        db.add(um)
        # Identity-bearing rows: audit entry + recommendation by U.
        db.add(models.AdminAuditLog(created_at=TS, actor_user_id=uid, action="x", summary="s"))
        db.add(models.BookRecommendation(hash_id=h, recommended_by=uid, recommended_at=TS))
        db.commit()
        um_id = um.id
    finally:
        db.close()

    db = TestSession()
    try:
        db.query(models.User).filter(models.User.id == uid).delete()
        db.commit()
    finally:
        db.close()

    db = TestSession()
    try:
        # Private data CASCADE-deleted.
        assert db.query(models.Favorite).filter_by(user_id=uid).count() == 0
        assert db.query(models.ReadingProgress).filter_by(user_id=uid).count() == 0
        assert db.query(models.BookRating).filter_by(user_id=uid).count() == 0
        assert db.query(models.BookComment).filter_by(user_id=uid).count() == 0
        assert db.query(models.Annotation).filter_by(user_id=uid).count() == 0
        assert db.query(models.DirectoryFavorite).filter_by(user_id=uid).count() == 0
        assert db.query(models.Playlist).filter_by(id=pl_id).first() is None
        assert db.query(models.PlaylistItem).filter_by(playlist_id=pl_id).count() == 0
        assert db.query(models.FeedbackThread).filter_by(public_id="UL-U").first() is None
        # V's thread survives; U's message in it is kept with author detached.
        assert db.query(models.FeedbackThread).filter_by(id=vt_id).first() is not None
        um2 = db.query(models.FeedbackMessage).filter_by(id=um_id).first()
        assert um2 is not None and um2.author_id is None
        # Audit + recommendation survive with identity detached.
        audit = db.query(models.AdminAuditLog).filter_by(action="x").first()
        assert audit is not None and audit.actor_user_id is None
        rec = db.query(models.BookRecommendation).filter_by(hash_id=h).first()
        assert rec is not None and rec.recommended_by is None
    finally:
        db.close()
