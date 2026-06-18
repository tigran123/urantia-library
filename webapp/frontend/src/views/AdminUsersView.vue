<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api'
import AdminNav from '../components/AdminNav.vue'
import { userInitials } from '../userDisplay'

const { t, locale } = useI18n({ useScope: 'global' })

const refreshStats = inject<() => void>('refreshStats', () => {})

type AdminUser = {
  id: number
  email: string
  is_admin: boolean
  clearance: number
  is_active: boolean
  avatar_url?: string | null
  real_name?: string | null
  last_seen_at?: string | null
  is_online?: boolean
}

type AdminSession = {
  jti: string
  user_id: number
  email: string
  ip_address: string | null
  user_agent: string | null
  created_at: string
  last_seen_at: string
  is_self: boolean
}

const users = ref<AdminUser[]>([])
const loading = ref(true)
const error = ref('')
const savingId = ref<number | null>(null)
const flash = ref<{ id: number; text: string } | null>(null)
// Server-side is_active per user, so a save can tell an enable/disable from
// an unrelated change (clearance, admin) and word the confirmation accordingly.
const origActive = new Map<number, boolean>()

// Client-side sort of the already-loaded user list. Defaults to email A→Z
// (matching the server's order); clicking a sortable header toggles direction.
type SortKey = 'email' | 'last_seen'
const sortKey = ref<SortKey>('email')
const sortDir = ref<'asc' | 'desc'>('asc')

const setSort = (key: SortKey) => {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    // Sensible per-column default: emails A→Z, last-seen most-recent first.
    sortDir.value = key === 'last_seen' ? 'desc' : 'asc'
  }
}

const sortArrow = (key: SortKey): string =>
  sortKey.value !== key ? '' : (sortDir.value === 'asc' ? '▲' : '▼')

const sortedUsers = computed<AdminUser[]>(() => {
  const dir = sortDir.value === 'asc' ? 1 : -1
  return [...users.value].sort((a, b) => {
    if (sortKey.value === 'email') return a.email.localeCompare(b.email) * dir
    // last_seen: parse to epoch ms; "Never" (null) always sorts last, then
    // fall back to email so the order is stable within equal timestamps.
    const ta = a.last_seen_at ? Date.parse(a.last_seen_at) : null
    const tb = b.last_seen_at ? Date.parse(b.last_seen_at) : null
    if (ta === null && tb === null) return a.email.localeCompare(b.email)
    if (ta === null) return 1
    if (tb === null) return -1
    if (ta === tb) return a.email.localeCompare(b.email)
    return (ta - tb) * dir
  })
})

const sessions = ref<AdminSession[]>([])
const sessionsLoading = ref(true)
const sessionsError = ref('')
const terminatingJti = ref<string | null>(null)
const sessionFlash = ref<string>('')

const loadUsers = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get('/admin/users')
    users.value = res.data
    origActive.clear()
    for (const u of users.value) origActive.set(u.id, u.is_active)
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

const loadSessions = async () => {
  sessionsLoading.value = true
  sessionsError.value = ''
  try {
    const res = await api.get('/admin/sessions')
    sessions.value = res.data
  } catch (err: any) {
    sessionsError.value = err.response?.data?.detail || err.message
  } finally {
    sessionsLoading.value = false
  }
  // Keep the footer's online-users / in-sessions count in sync with the table.
  refreshStats()
}

const terminateSession = async (s: AdminSession) => {
  const msg = t('admin.session_terminate_confirm', {
    email: s.email,
    ip: s.ip_address || '?',
  })
  if (!confirm(msg)) return
  terminatingJti.value = s.jti
  try {
    await api.delete(`/admin/sessions/${s.jti}`)
    sessionFlash.value = t('admin.session_terminated')
    setTimeout(() => { sessionFlash.value = '' }, 4000)
    await loadSessions()
  } catch (err: any) {
    alert(err.response?.data?.detail || err.message)
    await loadSessions()
  } finally {
    terminatingJti.value = null
  }
}

const getFullUrl = (url: string | null | undefined): string => {
  if (!url) return ''
  return (api.defaults.baseURL?.replace('/api', '') || '') + url
}

// Session timestamps arrive from the server as ISO-8601 UTC (…+00:00). Render
// them *in UTC* (not the viewer's local zone) with a 24h clock and a trailing
// "Z", so an admin reading the panel — possibly from another machine/timezone —
// sees an unambiguous, timezone-stable value. Date part keeps the app's
// "D MMM YYYY" convention (Russian " г." suffix stripped).
const fmtTime = (iso: string): string => {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const intlLocale = locale.value === 'ru' ? 'ru-RU' : 'en-GB'
  const date = new Intl.DateTimeFormat(intlLocale, {
    timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric',
  }).format(d).replace(/\s*г\.?$/u, '')
  const time = new Intl.DateTimeFormat(intlLocale, {
    timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(d)
  return `${date}, ${time}Z`
}

const saveUser = async (u: AdminUser) => {
  savingId.value = u.id
  const wasActive = origActive.get(u.id)
  try {
    const res = await api.put(`/admin/users/${u.id}/clearance`, {
      clearance: u.clearance,
      is_admin: u.is_admin,
      is_active: u.is_active,
    })
    Object.assign(u, res.data)
    origActive.set(u.id, u.is_active)
    const text = wasActive !== undefined && wasActive !== u.is_active
      ? t(u.is_active ? 'admin.user_enabled' : 'admin.user_disabled', { email: u.email })
      : t('admin.user_saved', { email: u.email })
    flash.value = { id: u.id, text }
    setTimeout(() => { if (flash.value?.id === u.id) flash.value = null }, 4000)
  } catch (err: any) {
    alert(err.response?.data?.detail || err.message)
    await loadUsers()
  } finally {
    savingId.value = null
  }
}

// Admin access is enforced by the router's /admin/* beforeEach guard, so by the
// time this view mounts the visitor is a confirmed admin — just load.
onMounted(() => {
  loadUsers()
  loadSessions()
})
</script>

<template>
  <div class="space-y-6">
    <AdminNav />

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <h2 class="text-lg font-semibold mb-4 text-gray-900 dark:text-white">{{ t('admin.users_section') }}</h2>

      <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ t('admin.loading') }}</div>
      <div v-else-if="error" class="text-red-600 dark:text-red-400">{{ error }}</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
            <th class="py-2 pr-2 cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-200" @click="setSort('email')">
              {{ t('admin.user_email') }}<span class="ml-1 text-xs">{{ sortArrow('email') }}</span>
            </th>
            <th class="py-2 pr-2 w-24">{{ t('admin.user_admin') }}</th>
            <th class="py-2 pr-2 w-32">{{ t('admin.user_clearance') }}</th>
            <th class="py-2 pr-2 w-20">{{ t('admin.user_active') }}</th>
            <th class="py-2 pr-2 w-44 cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-200" @click="setSort('last_seen')">
              {{ t('admin.user_last_seen') }}<span class="ml-1 text-xs">{{ sortArrow('last_seen') }}</span>
            </th>
            <th class="py-2"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in sortedUsers" :key="u.id" class="border-b border-gray-100 dark:border-gray-700/60">
            <td class="py-2 pr-2 text-gray-900 dark:text-gray-100">
              <div class="flex items-center gap-3">
                <img
                  v-if="u.avatar_url"
                  :src="getFullUrl(u.avatar_url)"
                  class="h-8 w-8 rounded-full object-cover border border-gray-200 dark:border-gray-700 flex-shrink-0"
                  alt=""
                />
                <span
                  v-else
                  class="h-8 w-8 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-semibold text-gray-600 dark:text-gray-200 flex-shrink-0"
                >{{ userInitials(u) }}</span>
                <div class="min-w-0">
                  <div v-if="u.real_name" class="truncate">{{ u.real_name }}</div>
                  <div class="truncate" :class="u.real_name ? 'text-xs text-gray-500 dark:text-gray-400' : ''">{{ u.email }}</div>
                </div>
              </div>
            </td>
            <td class="py-2 pr-2">
              <input type="checkbox" v-model="u.is_admin" />
            </td>
            <td class="py-2 pr-2">
              <input
                type="number"
                v-model.number="u.clearance"
                min="0"
                class="w-24 px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
              />
            </td>
            <td class="py-2 pr-2">
              <input type="checkbox" v-model="u.is_active" />
            </td>
            <td class="py-2 pr-2 text-gray-700 dark:text-gray-300">
              <span v-if="u.is_online" class="inline-flex items-center gap-1.5">
                <span class="inline-block h-2 w-2 rounded-full bg-emerald-500"></span>
                {{ t('admin.user_online') }}
              </span>
              <span v-else-if="u.last_seen_at">{{ fmtTime(u.last_seen_at) }}</span>
              <span v-else class="text-gray-400 dark:text-gray-500">{{ t('admin.user_never_seen') }}</span>
            </td>
            <td class="py-2">
              <div class="flex items-center gap-3">
                <button
                  @click="saveUser(u)"
                  :disabled="savingId === u.id"
                  class="px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 focus:outline-none"
                >
                  {{ savingId === u.id ? t('admin.saving') : t('admin.save') }}
                </button>
                <transition
                  enter-active-class="transition-opacity duration-200"
                  leave-active-class="transition-opacity duration-500"
                  enter-from-class="opacity-0"
                  leave-to-class="opacity-0"
                >
                  <span
                    v-if="flash && flash.id === u.id"
                    class="text-sm text-emerald-700 dark:text-emerald-400"
                  >{{ flash.text }}</span>
                </transition>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold text-gray-900 dark:text-white">{{ t('admin.sessions_section') }}</h2>
        <div class="flex items-center gap-3">
          <transition
            enter-active-class="transition-opacity duration-200"
            leave-active-class="transition-opacity duration-500"
            enter-from-class="opacity-0"
            leave-to-class="opacity-0"
          >
            <span v-if="sessionFlash" class="text-sm text-emerald-700 dark:text-emerald-400">{{ sessionFlash }}</span>
          </transition>
          <button
            @click="loadSessions"
            :disabled="sessionsLoading"
            class="px-3 py-1 text-sm bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 rounded hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 focus:outline-none"
          >
            {{ t('admin.session_refresh') }}
          </button>
        </div>
      </div>

      <div v-if="sessionsLoading" class="text-gray-500 dark:text-gray-400">{{ t('admin.loading') }}</div>
      <div v-else-if="sessionsError" class="text-red-600 dark:text-red-400">{{ sessionsError }}</div>
      <div v-else-if="sessions.length === 0" class="text-gray-500 dark:text-gray-400">{{ t('admin.sessions_empty') }}</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
            <th class="py-2 pr-2">{{ t('admin.session_email') }}</th>
            <th class="py-2 pr-2 w-32">{{ t('admin.session_ip') }}</th>
            <th class="py-2 pr-2">{{ t('admin.session_user_agent') }}</th>
            <th class="py-2 pr-2 w-44">{{ t('admin.session_created') }}</th>
            <th class="py-2 pr-2 w-44">{{ t('admin.session_last_seen') }}</th>
            <th class="py-2 w-28"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sessions" :key="s.jti" class="border-b border-gray-100 dark:border-gray-700/60">
            <td class="py-2 pr-2 text-gray-900 dark:text-gray-100">
              {{ s.email }}
              <span v-if="s.is_self" class="ml-2 text-xs text-gray-500 dark:text-gray-400">({{ t('admin.session_self_label') }})</span>
            </td>
            <td class="py-2 pr-2 text-gray-700 dark:text-gray-300 font-mono">{{ s.ip_address || '—' }}</td>
            <td class="py-2 pr-2 text-gray-700 dark:text-gray-300 truncate max-w-xs" :title="s.user_agent || ''">
              {{ s.user_agent || '—' }}
            </td>
            <td class="py-2 pr-2 text-gray-700 dark:text-gray-300">{{ fmtTime(s.created_at) }}</td>
            <td class="py-2 pr-2 text-gray-700 dark:text-gray-300">{{ fmtTime(s.last_seen_at) }}</td>
            <td class="py-2">
              <button
                @click="terminateSession(s)"
                :disabled="s.is_self || terminatingJti === s.jti"
                class="px-3 py-1 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none"
              >
                {{ t('admin.session_terminate') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
