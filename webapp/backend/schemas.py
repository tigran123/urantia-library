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
    is_active: Optional[bool] = None

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
    cover_url: Optional[str] = None
    last_verified_at: Optional[str] = None
    last_verified_ok: Optional[bool] = None
    last_verified_mode: Optional[str] = None
    last_verified_error: Optional[str] = None

    class Config:
        from_attributes = True


class UploadStagingResponse(BaseModel):
    staging_id: str
    hash: str
    size: int
    format: str
    cover_url: Optional[str] = None
    extracted_metadata: BookUpdate


class UploadCommitRequest(BaseModel):
    staging_id: str
    metadata: BookUpdate
    top_dir: str
    subpath: str = ""
    clearance: int = 100
    needs_review: bool = False
    filename: Optional[str] = None


class UploadDuplicateError(BaseModel):
    existing: AdminBookDetail


class DirListing(BaseModel):
    path: str
    dirs: List[str]


class CoverUpdateResponse(BaseModel):
    cover_url: str


class IntegrityCheckResult(BaseModel):
    hash_id: str
    mode: str
    ok: bool
    error: Optional[str] = None
    checks: List[dict] = []
    verified_at: str
    title: Optional[str] = None
    original_filename: Optional[str] = None
    db_update_failed: bool = False


class IntegrityJobCreate(BaseModel):
    scope: str  # 'all' | 'hash_ids'
    hash_ids: Optional[List[str]] = None
    mode: str = 'quick'  # 'quick' | 'full'


class IntegrityJobSummary(BaseModel):
    job_id: str
    status: str
    mode: str
    total: int
    processed: int
    ok_count: int
    fail_count: int
    started_at: str
    finished_at: Optional[str] = None
    error: Optional[str] = None


class IntegrityJobDetail(IntegrityJobSummary):
    failures: List[IntegrityCheckResult] = []
    all_results: Optional[List[IntegrityCheckResult]] = None
    all_results_truncated: bool = False

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


class RatingCreate(BaseModel):
    rating: int  # 1..5


class RatingResponse(BaseModel):
    """The caller's own rating for a book; `rating` is None when not rated."""
    hash_id: str
    rating: Optional[int] = None


class BookRatingStats(BaseModel):
    avg_rating: Optional[float] = None
    rating_count: int = 0


class CommentCreate(BaseModel):
    body: str
    parent_id: Optional[int] = None


class CommentUpdate(BaseModel):
    body: str


class CommentNode(BaseModel):
    """A comment as shown on the book page. Top-level nodes may carry the
    author's star rating and a list of replies; replies have neither."""
    id: int
    author_name: str
    body: str
    status: str          # 'pending' | 'approved'
    created_at: str
    is_own: bool
    rating: Optional[int] = None
    replies: List["CommentNode"] = []


class AdminCommentItem(BaseModel):
    """A comment in the admin moderation queue, with parent/book context."""
    id: int
    hash_id: str
    book_title: Optional[str] = None
    book_path: Optional[str] = None
    author_name: str
    body: str
    status: str
    parent_id: Optional[int] = None
    parent_snippet: Optional[str] = None
    created_at: str


class MoveRequest(BaseModel):
    src: str
    dst: str


class MoveItem(BaseModel):
    src: str
    dst: str
    hash_id: str


class MoveResponse(BaseModel):
    src: str
    dst: str
    kind: str            # 'file' | 'directory'
    dry_run: bool
    moved: List[MoveItem] = []
    errors: List[dict] = []
    skipped: List[dict] = []
