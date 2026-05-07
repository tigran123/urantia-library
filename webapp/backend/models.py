from sqlalchemy import Boolean, Column, Integer, String, ForeignKey
from database import Base
import uuid

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)

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

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    item_path = Column(String, nullable=False, index=True)

class ReadingProgress(Base):
    __tablename__ = "reading_progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    item_path = Column(String, nullable=False, index=True)
    location = Column(String, nullable=False)
