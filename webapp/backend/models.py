from sqlalchemy import Boolean, Column, Integer, String
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
