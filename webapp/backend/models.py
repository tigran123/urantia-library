from sqlalchemy import Boolean, Column, Integer, String, ForeignKey, UniqueConstraint
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

class BookLocation(Base):
    __tablename__ = "book_locations"

    hash_id = Column(String, ForeignKey("books.id"), nullable=False, index=True)
    symlink_path = Column(String, primary_key=True)
