<script setup lang="ts">
import { ref, onMounted, computed, inject, watch, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api'

const { t } = useI18n({ useScope: 'global' })
const currentUser = inject<Ref<{ search_per_page?: number | null } | null>>(
  'currentUser',
  ref(null) as unknown as Ref<{ search_per_page?: number | null } | null>,
)

// Re-fetch from page 1 when the user edits Settings → Results per page.
// The backend default reads users.search_per_page on each call, so we just
// need to retrigger the request after the modal saves.
watch(() => currentUser.value?.search_per_page, () => load(1))

interface ActivityEvent {
  id: number
  ts: string
  kind: string
  path: string | null
  hash_id: string | null
  ip: string
  user_agent: string | null
  geo_country: string | null
  geo_city: string | null
  extra: Record<string, any> | null
}

interface ActivityPage {
  page: number
  per_page: number
  total: number
  total_pages: number
  events: ActivityEvent[]
}

const events = ref<ActivityEvent[]>([])
const page = ref(1)
const totalPages = ref(0)
const total = ref(0)
const loading = ref(false)
const errorMsg = ref('')
const deleteState = ref<'idle' | 'confirming' | 'deleting' | 'done'>('idle')

async function load(p: number = 1) {
  loading.value = true
  errorMsg.value = ''
  try {
    // Backend defaults per_page to users.search_per_page (the same setting
    // the Search results use), so we don't pass it.
    const { data } = await api.get<ActivityPage>('/me/activity', { params: { page: p } })
    events.value = data.events
    page.value = data.page
    totalPages.value = data.total_pages
    total.value = data.total
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || t('myActivity.loadError')
  } finally {
    loading.value = false
  }
}

async function exportJson() {
  try {
    const { data } = await api.get('/me/activity', { params: { format: 'json' } })
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `my-activity-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || t('myActivity.exportError')
  }
}

async function deleteAll() {
  if (deleteState.value === 'idle') {
    deleteState.value = 'confirming'
    return
  }
  deleteState.value = 'deleting'
  try {
    await api.delete('/me/activity')
    deleteState.value = 'done'
    await load(1)
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || t('myActivity.deleteError')
    deleteState.value = 'idle'
  }
}

const formatExtra = (extra: Record<string, any> | null): string => {
  if (!extra) return ''
  try {
    return JSON.stringify(extra)
  } catch {
    return ''
  }
}

onMounted(() => load(1))

const hasEvents = computed(() => events.value.length > 0)
</script>

<template>
  <div class="px-4 py-8 text-gray-800 dark:text-gray-200">
    <h1 class="text-2xl font-bold mb-2">{{ t('myActivity.title') }}</h1>
    <p class="text-sm text-gray-600 dark:text-gray-400 mb-6">{{ t('myActivity.subtitle') }}</p>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800 rounded text-sm">
      {{ errorMsg }}
    </div>

    <div class="flex flex-wrap items-center gap-3 mb-4">
      <span class="text-sm text-gray-600 dark:text-gray-400">
        {{ t('myActivity.totalEvents', { n: total }) }}
      </span>
      <button
        type="button"
        class="px-3 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
        @click="exportJson"
      >
        {{ t('myActivity.exportBtn') }}
      </button>
      <button
        type="button"
        :class="[
          'px-3 py-1 text-sm rounded border',
          deleteState === 'confirming'
            ? 'border-red-600 bg-red-600 text-white hover:bg-red-700'
            : 'border-red-300 text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30'
        ]"
        :disabled="deleteState === 'deleting'"
        @click="deleteAll"
      >
        {{ deleteState === 'confirming' ? t('myActivity.deleteConfirm') :
           deleteState === 'deleting' ? t('myActivity.deleting') :
           deleteState === 'done' ? t('myActivity.deletedDone') :
           t('myActivity.deleteBtn') }}
      </button>
      <button
        v-if="deleteState === 'confirming'"
        type="button"
        class="px-3 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
        @click="deleteState = 'idle'"
      >
        {{ t('myActivity.cancel') }}
      </button>
    </div>

    <div v-if="loading" class="text-sm text-gray-500">{{ t('myActivity.loading') }}</div>

    <div v-else-if="!hasEvents" class="text-sm text-gray-500 italic py-8 text-center">
      {{ t('myActivity.empty') }}
    </div>

    <div v-else class="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded">
      <table class="w-full text-sm">
        <thead class="bg-gray-50 dark:bg-gray-800 text-left">
          <tr>
            <th class="px-3 py-2 font-medium">{{ t('myActivity.col.ts') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('myActivity.col.kind') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('myActivity.col.path') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('myActivity.col.ip') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('myActivity.col.geo') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('myActivity.col.extra') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ev in events" :key="ev.id" class="border-t border-gray-100 dark:border-gray-700">
            <td class="px-3 py-2 whitespace-nowrap font-mono text-xs">{{ ev.ts }}</td>
            <td class="px-3 py-2">{{ ev.kind }}</td>
            <td class="px-3 py-2 max-w-md truncate font-mono text-xs" :title="ev.path || ''">{{ ev.path }}</td>
            <td class="px-3 py-2 font-mono text-xs">{{ ev.ip }}</td>
            <td class="px-3 py-2">
              <template v-if="ev.geo_country || ev.geo_city">
                {{ [ev.geo_city, ev.geo_country].filter(Boolean).join(', ') }}
              </template>
              <template v-else>—</template>
            </td>
            <td class="px-3 py-2 max-w-xs truncate font-mono text-xs text-gray-500" :title="formatExtra(ev.extra)">
              {{ formatExtra(ev.extra) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="totalPages > 1" class="flex items-center justify-center gap-2 mt-4 text-sm">
      <button
        type="button"
        :disabled="page <= 1"
        class="px-3 py-1 border border-gray-300 dark:border-gray-600 rounded disabled:opacity-50"
        @click="load(page - 1)"
      >
        ‹ {{ t('myActivity.prev') }}
      </button>
      <span class="text-gray-600 dark:text-gray-400">{{ t('myActivity.pageOf', { p: page, n: totalPages }) }}</span>
      <button
        type="button"
        :disabled="page >= totalPages"
        class="px-3 py-1 border border-gray-300 dark:border-gray-600 rounded disabled:opacity-50"
        @click="load(page + 1)"
      >
        {{ t('myActivity.next') }} ›
      </button>
    </div>
  </div>
</template>
