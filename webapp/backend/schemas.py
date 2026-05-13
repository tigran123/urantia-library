from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    email: EmailStr
    source: Optional[str] = None
    purpose: Optional[str] = None

class UserResponse(BaseModel):
    email: EmailStr
    avatar_url: Optional[str] = None

class Message(BaseModel):
    message: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserSetPassword(BaseModel):
    token: str
    password: str

class FavoriteCreate(BaseModel):
    item_path: str

class FavoriteResponse(BaseModel):
    id: int
    user_id: int
    item_path: str

    class Config:
        from_attributes = True

class ReadingProgressCreate(BaseModel):
    item_path: str
    location: str

class ReadingProgressResponse(BaseModel):
    id: int
    user_id: int
    item_path: str
    location: str

    class Config:
        from_attributes = True
