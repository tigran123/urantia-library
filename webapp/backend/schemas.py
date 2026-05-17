from pydantic import BaseModel, EmailStr
from typing import List, Optional

class UserCreate(BaseModel):
    email: EmailStr
    source: Optional[str] = None
    purpose: Optional[str] = None

class UserResponse(BaseModel):
    email: EmailStr
    avatar_url: Optional[str] = None
    search_per_page: Optional[int] = None
    is_admin: bool = False
    clearance: int = 0

class UserSettingsUpdate(BaseModel):
    search_per_page: Optional[int] = None

class AdminUserSummary(BaseModel):
    id: int
    email: EmailStr
    is_admin: bool
    clearance: int
    is_active: bool

    class Config:
        from_attributes = True

class UserClearanceUpdate(BaseModel):
    clearance: Optional[int] = None
    is_admin: Optional[bool] = None

class BookClearanceUpdate(BaseModel):
    clearance: int

class BulkBookClearanceUpdate(BaseModel):
    hash_ids: List[str]
    clearance: int

class BookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    publisher: Optional[str] = None
    published: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    series: Optional[str] = None
    languages: Optional[str] = None
    identifiers: Optional[str] = None
    clearance: Optional[int] = None
    needs_review: Optional[bool] = None

class AdminBookDetail(BaseModel):
    id: str
    title: Optional[str] = None
    author: Optional[str] = None
    publisher: Optional[str] = None
    published: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    series: Optional[str] = None
    languages: Optional[str] = None
    identifiers: Optional[str] = None
    original_filename: str
    clearance: int
    needs_review: bool = False
    locations: List[str] = []

    class Config:
        from_attributes = True

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
