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

export default api
