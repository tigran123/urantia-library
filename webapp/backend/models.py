from sqlalchemy import Boolean, Column, Float, Integer, String, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import text
from database import Base
import uuid

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    clearance = Column(Integer, nullable=False, default=0)
    avatar_url = Column(String, nullable=True)
    real_name = Column(String, nullable=True)
    search_per_page = Column(Integer, nullable=True)
    accepted_legal_at = Column(String, nullable=True)   # ISO-8601 UTC; NULL = not yet accepted current Privacy/ToS

class RegistrationRequest(Base):
    __tablename__ = "registration_requests"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    status = Column(String, default="pending") # 'pending' or 'approved'
    source = Column(String, nullable=True)   # Optional context: where did they hear about the library
    purpose = Column(String, nullable=True)  # Optional context: purpose for registering
    token = Column(String, unique=True, index=True, default=lambda: str(uuid.uuid4()))
    accepted_legal_at = Column(String, nullable=True)   # ISO-8601 UTC; stamped at submit. NULL on a row = legacy pre-0003 request.
    language = Column(String, nullable=True)            # ISO-639-1 ('en' | 'ru'); NULL on a row = legacy pre-0002 request, treated as English by the email helpers.

class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "hash_id"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    hash_id = Column(String, ForeignKey("books.id"), nullable=False, index=True)

class DirectoryFavorite(Base):
    """Bookmarked directories. Keyed by `path` (relative to BOOKS_DIR) rather
    than a hash — directories aren't content-addressed."""
    __tablename__ = "directory_favorites"
    __table_args__ = (UniqueConstraint("user_id", "path"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    path = Column(String, nullable=False)

class ReadingProgress(Base):
    __tablename__ = "reading_progress"
    __table_args__ = (UniqueConstraint("user_id", "hash_id"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    hash_id = Column(String, ForeignKey("books.id"), nullable=False, index=True)
    location = Column(String, nullable=False)
    # 0.0..1.0; NULL when the viewer can't compute a meaningful position
    # (e.g. raw-mode text/image viewers). Surfaced on /api/favorites so the
    # bookshelf can render a progress bar.
    percent = Column(Float, nullable=True)

class Book(Base):
    __tablename__ = "books"

    id = Column(String, primary_key=True, index=True)
    title = Column(String)
    author = Column(String)
    publisher = Column(String)
    published = Column(String)
    description = Column(String)
    tags = Column(String)
    series = Column(String)
    languages = Column(String)
    identifiers = Column(String)
    original_filename = Column(String, nullable=False)
    needs_review = Column(Boolean, default=False)
    clearance = Column(Integer, nullable=False, default=100)
    last_verified_at = Column(String, nullable=True)
    last_verified_ok = Column(Boolean, nullable=True)
    last_verified_mode = Column(String, nullable=True)
    last_verified_error = Column(String, nullable=True)
    import_date = Column(String, nullable=False)
    size = Column(Integer, nullable=True)

class BookLocation(Base):
    __tablename__ = "book_locations"

    hash_id = Column(String, ForeignKey("books.id"), nullable=False, index=True)
    symlink_path = Column(String, primary_key=True)


class BookRating(Base):
    """One 1-5 star rating per user per book. Not moderated — a rating counts
    toward the book's average as soon as it is submitted."""
    __tablename__ = "book_ratings"
    __table_args__ = (UniqueConstraint("user_id", "hash_id"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    hash_id = Column(String, ForeignKey("books.id"), nullable=False, index=True)
    rating = Column(Integer, nullable=False)  # 1..5
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class BookComment(Base):
    """Moderated text comment. parent_id NULL = top-level comment; a non-NULL
    parent_id marks a reply. Replies cannot be replied to (one level only)."""
    __tablename__ = "book_comments"
    __table_args__ = (
        # One top-level comment per user per book; replies are unrestricted.
        Index(
            "ix_book_comments_one_toplevel", "user_id", "hash_id",
            unique=True, sqlite_where=text("parent_id IS NULL"),
        ),
        Index("idx_book_comments_hash_status", "hash_id", "status"),
        Index("idx_book_comments_parent", "parent_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    hash_id = Column(String, ForeignKey("books.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("book_comments.id"), nullable=True)
    body = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")  # 'pending' | 'approved'
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class AppMeta(Base):
    """Small key/value store for app-wide state (e.g. moderation digest throttle)."""
    __tablename__ = "app_meta"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)


class Annotation(Base):
    """In-viewer annotation: highlight + optional note anchored to a text
    selection in a book. Private annotations live with status='approved' (the
    field only gates public visibility). Public annotations start as 'pending'
    and an admin promotes them to 'approved'."""
    __tablename__ = "annotations"
    __table_args__ = (
        Index("idx_annotations_hash_status", "hash_id", "status"),
        Index("idx_annotations_user", "user_id"),
    )

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hash_id       = Column(String, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    anchor        = Column(Text, nullable=False)          # JSON; per-format selector
    selected_text = Column(Text, nullable=False)
    text_prefix   = Column(Text, nullable=True)
    text_suffix   = Column(Text, nullable=True)
    body          = Column(Text, nullable=True)           # NULL = plain highlight
    is_public     = Column(Boolean, nullable=False, default=False)
    status        = Column(String, nullable=False, default="approved")
    created_at    = Column(String, nullable=False)
    updated_at    = Column(String, nullable=False)


class FeedbackThread(Base):
    __tablename__ = "feedback_threads"

    id                = Column(Integer, primary_key=True, index=True)
    public_id         = Column(String, unique=True, index=True, nullable=False)
    user_id           = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category          = Column(String, nullable=False)
    book_subcategory  = Column(String, nullable=True)
    subject           = Column(String, nullable=False)
    status            = Column(String, nullable=False, default="new", index=True)
    book_hash_id      = Column(String, ForeignKey("books.id"), nullable=True, index=True)
    book_page         = Column(Integer, nullable=True)
    diag              = Column(Text, nullable=True)        # JSON string
    assigned_admin_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    digested_at       = Column(String, nullable=True, index=True)
    archived_at       = Column(String, nullable=True)
    created_at        = Column(String, nullable=False)
    updated_at        = Column(String, nullable=False)


class FeedbackRecipient(Base):
    """Empty for a given thread ⇒ broadcast. Non-empty ⇒ directed."""
    __tablename__ = "feedback_recipients"

    thread_id = Column(Integer, ForeignKey("feedback_threads.id", ondelete="CASCADE"), primary_key=True)
    admin_id  = Column(Integer, ForeignKey("users.id"), primary_key=True)


class FeedbackMessage(Base):
    __tablename__ = "feedback_messages"

    id         = Column(Integer, primary_key=True, index=True)
    thread_id  = Column(Integer, ForeignKey("feedback_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id  = Column(Integer, ForeignKey("users.id"), nullable=False)
    kind       = Column(String, nullable=False)            # 'message' | 'admin' | 'internal' | 'status'
    body       = Column(Text, nullable=False)
    created_at = Column(String, nullable=False)


class FeedbackAttachment(Base):
    __tablename__ = "feedback_attachments"

    id           = Column(Integer, primary_key=True, index=True)
    thread_id    = Column(Integer, ForeignKey("feedback_threads.id", ondelete="CASCADE"), nullable=False)
    filename     = Column(String, nullable=False)
    stored_path  = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    bytes        = Column(Integer, nullable=False)
    created_at   = Column(String, nullable=False)


class UserNotificationPrefs(Base):
    __tablename__ = "user_notification_prefs"

    user_id              = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email_on_reply       = Column(Boolean, nullable=False, default=True)
    email_on_status      = Column(Boolean, nullable=False, default=True)
    email_weekly_summary = Column(Boolean, nullable=False, default=False)
    updated_at           = Column(String, nullable=False)


class AdminFeedbackSettings(Base):
    __tablename__ = "admin_feedback_settings"

    id                    = Column(Integer, primary_key=True)   # always 1
    digest_interval_hours = Column(Integer, nullable=False, default=6)
    min_batch_size        = Column(Integer, nullable=False, default=1)
    urgent_bypass         = Column(Boolean, nullable=False, default=True)
    extra_recipients      = Column(Text, nullable=False, default="")
    updated_at            = Column(String, nullable=False)


class AdminAuditLog(Base):
    """Append-only log of administrative actions (uploads, edits, deletes,
    moderation decisions, …). Surfaced in the Admin panel as both a timeline
    and a per-admin leaderboard. Rows are written via _audit() in main.py
    within the caller's transaction — a rolled-back action leaves no audit
    ghost. Never UPDATEd or DELETEd in normal operation."""
    __tablename__ = "admin_audit_log"
    __table_args__ = (
        Index("idx_admin_audit_log_created_at", "created_at"),
        Index("idx_admin_audit_log_actor",      "actor_user_id"),
        Index("idx_admin_audit_log_action",     "action"),
    )

    id            = Column(Integer, primary_key=True, index=True)
    created_at    = Column(String, nullable=False)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action        = Column(String, nullable=False)
    target_kind   = Column(String, nullable=True)
    target_id     = Column(String, nullable=True)
    summary       = Column(String, nullable=False)
    details_json  = Column(Text, nullable=True)


class UsageEvent(Base):
    """Per-request usage telemetry: page views, book opens, searches, logins,
    registrations. Written from the request path via _record_usage_event() in
    main.py (try/except wrapped so telemetry can never break a request).

    Personal data under GDPR (IP address; see CJEU Breyer C-582/14). Retention
    is 24 months, enforced daily by scripts/prune_usage_events.sh. The
    user-facing surface lives at #/account/activity (GET/DELETE /api/me/activity)
    and the admin surface at /api/admin/usage/*. See the Privacy Policy for the
    full data-flow description."""
    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_ts",      "ts"),
        Index("ix_usage_events_user_ts", "user_id", "ts"),
        Index("ix_usage_events_ip_ts",   "ip", "ts"),
        Index("ix_usage_events_hash_ts", "hash_id", "ts"),
        Index("ix_usage_events_kind_ts", "kind", "ts"),
    )

    id          = Column(Integer, primary_key=True, index=True)
    ts          = Column(String, nullable=False)                                                  # ISO-8601 UTC
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)     # NULL = guest or detached
    session_jti = Column(String, nullable=True)
    ip          = Column(String, nullable=False)
    user_agent  = Column(String, nullable=True)
    geo_country = Column(String, nullable=True)
    geo_city    = Column(String, nullable=True)
    kind        = Column(String, nullable=False)                                                  # 'page'|'book_open'|'search'|'login'|'register'
    path        = Column(String, nullable=True)
    hash_id     = Column(String, ForeignKey("books.id", ondelete="SET NULL"), nullable=True)
    extra_json  = Column(Text, nullable=True)
