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

      // Redirect to login if unauthorized AND not already on a public page
      if (router.currentRoute.value.name && !publicRoutes.includes(router.currentRoute.value.name.toString())) {
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

export default api
