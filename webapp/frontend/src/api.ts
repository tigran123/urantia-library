import axios from 'axios'
import router from './router'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  withCredentials: true,
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      // Public routes that don't require an active session
      const publicRoutes = ['login', 'register', 'set-password']

      // The guest landing flow probes /me on every page load; a 401 there just
      // means "browsing anonymously" and must NOT bounce the visitor to login.
      const isMeProbe = error.config?.url === '/me'

      // Redirect to login if unauthorized AND not already on a public page
      if (!isMeProbe && router.currentRoute.value.name && !publicRoutes.includes(router.currentRoute.value.name.toString())) {
        router.push({ name: 'login' })
      }
    }
    return Promise.reject(error)
  }
)

export type IntegrityMode = 'quick' | 'full'

export interface IntegrityCheck {
  name: string
  ok: boolean
  detail: any
}

export interface IntegrityCheckResult {
  hash_id: string
  mode: IntegrityMode
  ok: boolean
  error: string | null
  checks: IntegrityCheck[]
  verified_at: string
  title: string | null
  original_filename: string | null
  db_update_failed: boolean
}

export interface IntegrityJobSummary {
  job_id: string
  status: 'running' | 'done' | 'cancelled' | 'error'
  mode: IntegrityMode
  total: number
  processed: number
  ok_count: number
  fail_count: number
  started_at: string
  finished_at: string | null
  error: string | null
}

export interface IntegrityJobDetail extends IntegrityJobSummary {
  failures: IntegrityCheckResult[]
  all_results: IntegrityCheckResult[] | null
  all_results_truncated: boolean
}

export const verifyBook = (hashId: string, mode: IntegrityMode) =>
  api.post<IntegrityCheckResult>(
    `/admin/integrity/verify/${encodeURIComponent(hashId)}`,
    null,
    { params: { mode } },
  )

export const startIntegrityJob = (payload: {
  scope: 'all' | 'hash_ids'
  hash_ids?: string[]
  mode: IntegrityMode
}) => api.post<IntegrityJobSummary>('/admin/integrity/jobs', payload)

export const getIntegrityJob = (jobId: string, include: 'failures' | 'all' = 'failures') =>
  api.get<IntegrityJobDetail>(
    `/admin/integrity/jobs/${encodeURIComponent(jobId)}`,
    { params: { include } },
  )

export const listIntegrityJobs = () =>
  api.get<{ jobs: IntegrityJobSummary[] }>('/admin/integrity/jobs')

export const cancelIntegrityJob = (jobId: string) =>
  api.delete<IntegrityJobSummary>(`/admin/integrity/jobs/${encodeURIComponent(jobId)}`)

export const searchHashIds = (q: string) =>
  api.get<{ hash_ids: string[]; total: number }>('/search/hash_ids', { params: { q } })

// ---------- Ratings & comments ----------

export interface CommentNode {
  id: number
  author_name: string
  body: string
  status: 'pending' | 'approved'
  created_at: string
  is_own: boolean
  rating: number | null
  replies: CommentNode[]
}

export interface AdminCommentItem {
  id: number
  hash_id: string
  book_title: string | null
  book_path: string | null
  author_name: string
  body: string
  status: string
  parent_id: number | null
  parent_snippet: string | null
  created_at: string
}

export interface AdminCommentsPage {
  comments: AdminCommentItem[]
  total: number
  page: number
  per_page: number
  total_pages: number
}

export const getMyRating = (hashId: string) =>
  api.get<{ hash_id: string; rating: number | null }>(
    `/books/${encodeURIComponent(hashId)}/rating`)

export const setMyRating = (hashId: string, rating: number) =>
  api.post<{ hash_id: string; rating: number | null }>(
    `/books/${encodeURIComponent(hashId)}/rating`, { rating })

export const deleteMyRating = (hashId: string) =>
  api.delete(`/books/${encodeURIComponent(hashId)}/rating`)

export const getComments = (hashId: string) =>
  api.get<{ comments: CommentNode[] }>(
    `/books/${encodeURIComponent(hashId)}/comments`)

export const postComment = (hashId: string, body: string, parentId?: number) =>
  api.post<{ id: number; status: string }>(
    `/books/${encodeURIComponent(hashId)}/comments`,
    { body, parent_id: parentId ?? null })

export const editComment = (commentId: number, body: string) =>
  api.put<{ id: number; status: string }>(`/comments/${commentId}`, { body })

export const deleteComment = (commentId: number) =>
  api.delete(`/comments/${commentId}`)

export const adminListComments = (status: 'pending' | 'recent' = 'pending', page = 1, perPage = 50) =>
  api.get<AdminCommentsPage>('/admin/comments', { params: { status, page, per_page: perPage } })

export const adminApproveComment = (commentId: number) =>
  api.post(`/admin/comments/${commentId}/approve`)

export const adminDeleteComment = (commentId: number) =>
  api.delete(`/admin/comments/${commentId}`)

// ---------- Feedback / contact-admin ----------

export type FeedbackStatus =
  | 'new' | 'open' | 'triage' | 'progress'
  | 'waiting' | 'resolved' | 'closed' | 'archived'

export type FeedbackInboxFilter = FeedbackStatus | 'all' | 'mine'

export type FeedbackCategory =
  | 'general' | 'bug' | 'feature' | 'book' | 'acquire' | 'other'

export type FeedbackBookSubcategory =
  | 'metadata' | 'corrupt' | 'copyright' | 'inappropriate' | 'duplicate'

export interface AdminBrief {
  id: number
  name: string
  email: string
}

export interface FeedbackRecipientBrief {
  id: number
  name: string
  is_you: boolean
}

export interface FeedbackThreadSummary {
  id: number
  public_id: string
  user_name: string
  user_email: string
  category: FeedbackCategory
  book_subcategory: FeedbackBookSubcategory | null
  subject: string
  status: FeedbackStatus
  book_hash_id: string | null
  book_title: string | null
  book_path: string | null
  recipients: FeedbackRecipientBrief[]
  is_broadcast: boolean
  reply_count: number
  has_unread: boolean
  archived: boolean
  created_at: string
  updated_at: string
}

export interface FeedbackMessageNode {
  id: number
  kind: 'message' | 'admin' | 'internal' | 'status'
  author_name: string
  is_admin: boolean
  is_own: boolean
  body: string
  created_at: string
}

export interface FeedbackAttachmentBrief {
  filename: string
  url: string
  bytes: number
  content_type: string
}

export interface FeedbackThreadDetail extends FeedbackThreadSummary {
  body: string
  book_page: number | null
  diag: Record<string, string> | null
  attachment: FeedbackAttachmentBrief | null
  messages: FeedbackMessageNode[]
  assigned_admin_name: string | null
}

export interface FeedbackCreatePayload {
  category: FeedbackCategory
  book_subcategory?: FeedbackBookSubcategory | null
  subject: string
  body: string
  book_hash_id?: string | null
  book_page?: number | null
  diag?: Record<string, string>
  recipient_admin_ids?: number[]
}

export const createFeedback = (p: FeedbackCreatePayload) =>
  api.post<{ id: number; public_id: string }>('/feedback', p)

export const uploadFeedbackAttachment = (threadId: number, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return api.post(`/feedback/${threadId}/attachment`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export const listMyFeedback = (status: FeedbackStatus | 'all' = 'all', page = 1, per_page = 50) =>
  api.get<{ total: number; items: FeedbackThreadSummary[] }>(
    '/feedback', { params: { status, page, per_page } })

export const getFeedbackThread = (publicId: string) =>
  api.get<FeedbackThreadDetail>(`/feedback/${encodeURIComponent(publicId)}`)

export const replyToFeedback = (
  threadId: number,
  body: string,
  opts: { internal?: boolean; new_status?: FeedbackStatus } = {},
) => api.post(`/feedback/${threadId}/reply`, { body, ...opts })

export const resolveMyFeedback = (threadId: number) =>
  api.post(`/feedback/${threadId}/resolve`)

export interface AdminFeedbackListResponse {
  total: number
  counts: Record<string, number>
  items: FeedbackThreadSummary[]
}

export const adminListFeedback = (
  status: FeedbackInboxFilter = 'new',
  page = 1, per_page = 50, q = '',
) => api.get<AdminFeedbackListResponse>('/admin/feedback', {
  params: { status, page, per_page, q },
})

export const listAdmins = () =>
  api.get<{ items: AdminBrief[] }>('/admins')

export const adminSetFeedbackStatus = (threadId: number, status: FeedbackStatus, archive = false) =>
  api.patch(`/admin/feedback/${threadId}/status`, { status, archive })

export const adminAssignFeedback = (threadId: number, assignee_id: number | null) =>
  api.post(`/admin/feedback/${threadId}/assign`, { assignee_id })

export const adminDeleteFeedback = (threadId: number) =>
  api.delete(`/admin/feedback/${threadId}`)

export interface AdminFeedbackSettings {
  digest_interval_hours: number
  min_batch_size: number
  urgent_bypass: boolean
  extra_recipients: string[]
  last_digest_at: string | null
  next_eligible_at: string | null
  pending_count: number
}

export const getAdminFeedbackSettings = () =>
  api.get<AdminFeedbackSettings>('/admin/feedback/settings')

export const setAdminFeedbackSettings = (p: Omit<AdminFeedbackSettings,
  'last_digest_at' | 'next_eligible_at' | 'pending_count'>) =>
  api.put('/admin/feedback/settings', p)

export const forceFeedbackDigest = () =>
  api.post<{ ok: true; sent: number }>('/admin/feedback/digest/force')

// ---------- Notification prefs ----------

export interface NotificationPrefs {
  email_on_reply: boolean
  email_on_status: boolean
  email_weekly_summary: boolean
}

export const getMyNotificationPrefs = () =>
  api.get<NotificationPrefs>('/users/me/notification-prefs')

export const setMyNotificationPrefs = (p: NotificationPrefs) =>
  api.put('/users/me/notification-prefs', p)

export default api
