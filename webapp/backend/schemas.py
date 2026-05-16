from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    email: EmailStr
    source: Optional[str] = None
    purpose: Optional[str] = None

class UserResponse(BaseModel):
    email: EmailStr
    avatar_url: Optional[str] = None
    search_per_page: Optional[int] = None

class UserSettingsUpdate(BaseModel):
    search_per_page: Optional[int] = None

class Message(BaseModel):
    message: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserSetPassword(BaseModel):
    token: str
    password: str

class FavoriteCreate(BaseModel):
    hash_id: str

class FavoriteResponse(BaseModel):
    id: int
    user_id: int
    hash_id: str

    class Config:
        from_attributes = True

class DirectoryFavoriteCreate(BaseModel):
    path: str

class DirectoryFavoriteResponse(BaseModel):
    id: int
    user_id: int
    path: str

    class Config:
        from_attributes = True

class ReadingProgressCreate(BaseModel):
    hash_id: str
    location: str

class ReadingProgressResponse(BaseModel):
    id: int
    user_id: int
    hash_id: str
    location: str

    class Config:
        from_attributes = True
