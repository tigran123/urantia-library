from sqlalchemy import Boolean, Column, Integer, String, Text, ForeignKey, UniqueConstraint, Index
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

class RegistrationRequest(Base):
    __tablename__ = "registration_requests"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    status = Column(String, default="pending") # 'pending' or 'approved'
    source = Column(String, nullable=True)   # Optional context: where did they hear about the library
    purpose = Column(String, nullable=True)  # Optional context: purpose for registering
    token = Column(String, unique=True, index=True, default=lambda: str(uuid.uuid4()))

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
