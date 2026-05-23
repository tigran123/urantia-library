<script setup lang="ts">
import { ref, onMounted, inject } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api from '../api'
import AdminNav from '../components/AdminNav.vue'

const { t } = useI18n({ useScope: 'global' })

type CurrentUser = { email: string, is_admin?: boolean } | null
const currentUser = inject<{ value: CurrentUser } | null>('currentUser', null)
const router = useRouter()

type AdminUser = {
  id: number
  email: string
  is_admin: boolean
  clearance: number
  is_active: boolean
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

const fmtTime = (iso: string): string => {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
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

onMounted(() => {
  if (!currentUser?.value?.is_admin) {
    router.replace('/')
    return
  }
  loadUsers()
  loadSessions()
})
</script>

<template>
  <div class="space-y-6 max-w-5xl mx-auto">
    <AdminNav />

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <h2 class="text-lg font-semibold mb-4 text-gray-900 dark:text-white">{{ t('admin.users_section') }}</h2>

      <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ t('admin.loading') }}</div>
      <div v-else-if="error" class="text-red-600 dark:text-red-400">{{ error }}</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
            <th class="py-2 pr-2">{{ t('admin.user_email') }}</th>
            <th class="py-2 pr-2 w-24">{{ t('admin.user_admin') }}</th>
            <th class="py-2 pr-2 w-32">{{ t('admin.user_clearance') }}</th>
            <th class="py-2 pr-2 w-20">{{ t('admin.user_active') }}</th>
            <th class="py-2"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" class="border-b border-gray-100 dark:border-gray-700/60">
            <td class="py-2 pr-2 text-gray-900 dark:text-gray-100">{{ u.email }}</td>
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
