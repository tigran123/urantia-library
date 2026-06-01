from pydantic import BaseModel, ConfigDict, EmailStr
from typing import Any, Dict, List, Optional

class UserCreate(BaseModel):
    email: EmailStr
    source: Optional[str] = None
    purpose: Optional[str] = None
    # The registration form's required "I accept the Privacy Policy and Terms"
    # checkbox; /api/register rejects with 400 when this is False.
    accepted_legal: bool = False
    # ISO-639-1 of the UI locale at submit time; the admin's approval/rejection
    # email is rendered in this language. /api/register normalizes unknown
    # values to NULL, which the email helpers treat as English.
    language: Optional[str] = None

class UserResponse(BaseModel):
    email: EmailStr
    avatar_url: Optional[str] = None
    real_name: Optional[str] = None
    search_per_page: Optional[int] = None
    is_admin: bool = False
    clearance: int = 0
    # LEGAL_VERSION the user last accepted; None for legacy rows. The frontend
    # gates on `legal_acceptance_current` rather than comparing this directly —
    # the comparison rule lives server-side so a client release isn't needed
    # if it ever changes.
    legal_version_accepted: Optional[str] = None
    legal_acceptance_current: bool = True

class UserSettingsUpdate(BaseModel):
    search_per_page: Optional[int] = None
    real_name: Optional[str] = None

class AdminUserSummary(BaseModel):
    id: int
    email: EmailStr
    is_admin: bool
    clearance: int
    is_active: bool
    avatar_url: Optional[str] = None
    real_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class UserClearanceUpdate(BaseModel):
    clearance: Optional[int] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None

class AdminSessionSummary(BaseModel):
    jti: str
    user_id: int
    email: EmailStr
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: str
    last_seen_at: str
    is_self: bool

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

    model_config = ConfigDict(from_attributes=True)


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
    # When several books are committed together (multi-volume "Commit all"), the
    # client sends a shared id so the backend folds them into ONE audit row.
    batch_id: Optional[str] = None


class TouchStagingRequest(BaseModel):
    staging_ids: List[str] = []


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
    real_name: Optional[str] = None
    # Privacy Policy + ToS consent at the set-password step. Defaults to False
    # so an older client tab from before this field was added gets a clean
    # 400 instead of silently completing without consent.
    accepted_legal: bool = False

class FavoriteCreate(BaseModel):
    hash_id: str

class FavoriteResponse(BaseModel):
    id: int
    user_id: int
    hash_id: str

    model_config = ConfigDict(from_attributes=True)

class DirectoryFavoriteCreate(BaseModel):
    path: str

class DirectoryFavoriteResponse(BaseModel):
    id: int
    user_id: int
    path: str

    model_config = ConfigDict(from_attributes=True)

class PlaylistCreate(BaseModel):
    name: str
    description: Optional[str] = None
    visibility: str = "private"   # 'private' | 'public'


class PlaylistUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[str] = None


class PlaylistItemAdd(BaseModel):
    """Add a book (book_hash_id) or a directory (dir_path) — exactly one."""
    book_hash_id: Optional[str] = None
    dir_path: Optional[str] = None


class PlaylistOrderUpdate(BaseModel):
    """Ordered list of playlist_item ids; must match the playlist's full set."""
    item_ids: List[int]


class ReadingProgressCreate(BaseModel):
    hash_id: str
    location: str
    percent: Optional[float] = None

class ReadingProgressResponse(BaseModel):
    id: int
    user_id: int
    hash_id: str
    location: str
    percent: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class RatingCreate(BaseModel):
    rating: int  # 1..5


class RatingResponse(BaseModel):
    """The caller's own rating for a book; `rating` is None when not rated."""
    hash_id: str
    rating: Optional[int] = None


class BookRatingStats(BaseModel):
    avg_rating: Optional[float] = None
    rating_count: int = 0


class RecommendationResponse(BaseModel):
    """Returned by POST /api/admin/books/{hash_id}/recommend. Names the
    admin who flipped the flag (`recommended_by_name` is real_name only —
    email is PII and these results flow to anonymous browsers, so the
    fallback is None rather than the admin's address) and surfaces the
    freshly-created symlink_path so the UI can patch `item.locations`
    locally without a full refetch."""
    hash_id: str
    recommended_by: int
    recommended_by_name: Optional[str] = None
    recommended_at: str
    symlink_path: Optional[str] = None    # path of the Recommended/* symlink; None when already-recommended


class BulkRecommendRequest(BaseModel):
    hash_ids: List[str]


class BulkRecommendResponse(BaseModel):
    recommended: int = 0      # number of books newly recommended in this batch
    unchanged: int = 0        # already-recommended, skipped silently
    errors: List[dict] = []   # [{hash_id, reason}]


class BulkUnrecommendResponse(BaseModel):
    unrecommended: int = 0    # number of books removed from Recommended in this batch
    unchanged: int = 0        # weren't recommended, skipped silently
    errors: List[dict] = []   # [{hash_id, reason}]


class UnrecommendResponse(BaseModel):
    """Returned by DELETE /api/admin/books/{hash_id}/recommend. `removed` lists
    the Recommended/* symlink paths the call actually unlinked — empty when the
    book wasn't recommended in the first place (idempotent no-op)."""
    ok: bool = True
    removed: List[str] = []


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


class AnnotationCreate(BaseModel):
    hash_id: str
    anchor: dict                          # serialized as JSON in the column
    selected_text: str
    text_prefix: Optional[str] = None
    text_suffix: Optional[str] = None
    body: Optional[str] = None
    is_public: bool = False


class AnnotationUpdate(BaseModel):
    """All fields optional — only supplied fields are touched. Editing body,
    anchor, or is_public on a public annotation flips status back to 'pending'
    (unless the editor is an admin)."""
    anchor: Optional[dict] = None
    selected_text: Optional[str] = None
    text_prefix: Optional[str] = None
    text_suffix: Optional[str] = None
    body: Optional[str] = None
    is_public: Optional[bool] = None


class AnnotationOut(BaseModel):
    id: int
    hash_id: str
    author_id: int
    author_name: str
    anchor: dict
    selected_text: str
    text_prefix: Optional[str] = None
    text_suffix: Optional[str] = None
    body: Optional[str] = None
    is_public: bool
    status: str                            # 'pending' | 'approved'
    is_own: bool
    created_at: str
    updated_at: str


class AdminAnnotationItem(BaseModel):
    """A pending public annotation in the admin moderation queue."""
    id: int
    hash_id: str
    book_title: Optional[str] = None
    book_path: Optional[str] = None
    author_name: str
    selected_text: str
    body: Optional[str] = None
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


# ==============================================================================
# Feedback / contact-admin
# ==============================================================================

class FeedbackCreate(BaseModel):
    category: str                                # one of _FEEDBACK_CATEGORIES
    book_subcategory: Optional[str] = None
    subject: str
    body: str
    book_hash_id: Optional[str] = None
    book_page: Optional[int] = None
    diag: Optional[dict] = None                  # sanitized server-side, never surfaced to the author
    recipient_admin_ids: List[int] = []          # honoured only when sender is admin


class FeedbackReply(BaseModel):
    body: str
    internal: bool = False                       # ignored for non-admin senders
    new_status: Optional[str] = None             # admin may flip status atomically


class FeedbackStatusUpdate(BaseModel):
    status: str
    archive: bool = False


class FeedbackAssign(BaseModel):
    assignee_id: Optional[int] = None            # null = unassign


class FeedbackMessageNode(BaseModel):
    id: int
    kind: str                                    # 'message' | 'admin' | 'internal' | 'status'
    author_name: str
    is_admin: bool
    is_own: bool
    body: str
    created_at: str


class AdminBrief(BaseModel):
    id: int
    name: str
    email: str


class FeedbackRecipientBrief(BaseModel):
    id: int
    name: str
    is_you: bool


class FeedbackAttachmentBrief(BaseModel):
    filename: str
    url: str
    bytes: int
    content_type: str


class FeedbackThreadSummary(BaseModel):
    id: int
    public_id: str
    user_name: str
    user_email: str
    category: str
    book_subcategory: Optional[str] = None
    subject: str
    status: str
    book_hash_id: Optional[str] = None
    book_title: Optional[str] = None
    book_path: Optional[str] = None
    recipients: List[FeedbackRecipientBrief] = []
    is_broadcast: bool = True
    reply_count: int = 0
    has_unread: bool = False
    archived: bool = False
    created_at: str
    updated_at: str


class FeedbackThreadDetail(FeedbackThreadSummary):
    body: str                                    # the original 'message' row
    book_page: Optional[int] = None
    diag: Optional[dict] = None                  # populated only when viewer.is_admin
    attachment: Optional[FeedbackAttachmentBrief] = None
    messages: List[FeedbackMessageNode] = []
    assigned_admin_name: Optional[str] = None


class NotificationPrefs(BaseModel):
    email_on_reply: bool = True
    email_on_status: bool = True
    email_weekly_summary: bool = False


class AdminFeedbackSettingsPayload(BaseModel):
    digest_interval_hours: int                   # 0|1|3|6|12|24
    min_batch_size: int                          # 1|3|5
    urgent_bypass: bool
    extra_recipients: List[str] = []


class AdminFeedbackSettingsResponse(AdminFeedbackSettingsPayload):
    last_digest_at: Optional[str] = None
    next_eligible_at: Optional[str] = None
    pending_count: int = 0


class UsageSettingsUpdate(BaseModel):
    """Payload for PUT /api/admin/usage/settings. The server treats this as
    the full new set — any kind not listed becomes disabled. Unknown kind
    names are silently dropped server-side."""
    enabled_kinds: List[str]


# --- Librarian audit log -----------------------------------------------------

class AuditActor(BaseModel):
    """Compact actor reference embedded in feed entries and leaderboard rows.
    Resolved server-side via a JOIN so the UI can render avatar + name without
    a second roundtrip per row.

    `email` is plain `str` (not `EmailStr`) deliberately: the audit log is the
    last-resort observability tool, so it must render even if a legacy user
    row has a malformed email, and the feed uses the empty string when the
    actor row was hard-deleted (see admin_audit_feed in main.py)."""
    id: int
    email: str
    real_name: Optional[str] = None
    avatar_url: Optional[str] = None


class AuditLogEntry(BaseModel):
    id: int
    created_at: str
    actor: AuditActor
    action: str
    target_kind: Optional[str] = None
    target_id: Optional[str] = None
    summary: str
    details: Optional[Dict[str, Any]] = None    # parsed from admin_audit_log.details_json


class AuditFeed(BaseModel):
    entries: List[AuditLogEntry]
    page: int
    per_page: int
    total: int                                  # total matching rows across all pages
    total_pages: int                            # max page number; 0 when total==0


class AuditLeaderEntry(BaseModel):
    actor: AuditActor
    totals: Dict[str, int]                      # action → count, e.g. {"book.upload": 12, "book.edit": 5}
    total: int                                  # sum of all action counts in `totals`


class AuditStats(BaseModel):
    since: str                                  # ISO-8601 UTC; entries with created_at >= this are counted (inclusive)
    leaders: List[AuditLeaderEntry]             # sorted by total desc
