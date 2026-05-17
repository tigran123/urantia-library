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

const users = ref<AdminUser[]>([])
const loading = ref(true)
const error = ref('')
const savingId = ref<number | null>(null)

const loadUsers = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get('/admin/users')
    users.value = res.data
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

const saveUser = async (u: AdminUser) => {
  savingId.value = u.id
  try {
    const res = await api.put(`/admin/users/${u.id}/clearance`, {
      clearance: u.clearance,
      is_admin: u.is_admin,
    })
    Object.assign(u, res.data)
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
            <td class="py-2 pr-2 text-gray-600 dark:text-gray-300">{{ u.is_active ? t('admin.yes') : t('admin.no') }}</td>
            <td class="py-2">
              <button
                @click="saveUser(u)"
                :disabled="savingId === u.id"
                class="px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 focus:outline-none"
              >
                {{ savingId === u.id ? t('admin.saving') : t('admin.save') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
