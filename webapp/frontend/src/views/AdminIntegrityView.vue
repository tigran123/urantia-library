<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import AdminNav from '../components/AdminNav.vue'
import {
  startIntegrityJob,
  listIntegrityJobs,
  getIntegrityJob,
  cancelIntegrityJob,
  type IntegrityJobSummary,
  type IntegrityJobDetail,
  type IntegrityMode,
  type IntegrityCheckResult,
} from '../api'
import { CheckCircleIcon, XCircleIcon, ArrowPathIcon, NoSymbolIcon } from '@heroicons/vue/24/outline'

const { t } = useI18n({ useScope: 'global' })
const route = useRoute()
const router = useRouter()

const jobs = ref<IntegrityJobSummary[]>([])
const error = ref('')
const starting = ref(false)
const selectedJobId = ref<string | null>((route.query.job as string) || null)
const detail = ref<IntegrityJobDetail | null>(null)
const includeAll = ref(false)

const runningJob = computed(() => jobs.value.find((j) => j.status === 'running') || null)

let pollTimer: number | null = null

const loadJobs = async () => {
  try {
    const res = await listIntegrityJobs()
    jobs.value = res.data.jobs
    if (selectedJobId.value) {
      await loadDetail()
    }
    error.value = ''
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || 'error'
  }
}

const loadDetail = async () => {
  if (!selectedJobId.value) return
  try {
    const res = await getIntegrityJob(selectedJobId.value, includeAll.value ? 'all' : 'failures')
    detail.value = res.data
  } catch (e: any) {
    if (e?.response?.status === 404) {
      detail.value = null
      selectedJobId.value = null
    }
  }
}

const startWholeLibrary = async (mode: IntegrityMode) => {
  starting.value = true
  error.value = ''
  try {
    const res = await startIntegrityJob({ scope: 'all', mode })
    selectedJobId.value = res.data.job_id
    router.replace({ query: { ...route.query, job: res.data.job_id } })
    await loadJobs()
  } catch (e: any) {
    const detailMsg = e?.response?.data?.detail
    if (detailMsg && typeof detailMsg === 'object' && detailMsg.reason === 'job_running') {
      selectedJobId.value = detailMsg.running_job_id
      router.replace({ query: { ...route.query, job: detailMsg.running_job_id } })
      await loadJobs()
    } else {
      error.value = typeof detailMsg === 'string' ? detailMsg : (e?.message || 'error')
    }
  } finally {
    starting.value = false
  }
}

const cancel = async (jobId: string) => {
  try {
    await cancelIntegrityJob(jobId)
    await loadJobs()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || 'error'
  }
}

const selectJob = async (jobId: string) => {
  selectedJobId.value = jobId
  detail.value = null
  router.replace({ query: { ...route.query, job: jobId } })
  await loadDetail()
}

const toggleAllResults = async () => {
  includeAll.value = !includeAll.value
  await loadDetail()
}

const startPolling = () => {
  if (pollTimer !== null) return
  pollTimer = window.setInterval(async () => {
    await loadJobs()
  }, runningJob.value ? 2000 : 10000)
}

const stopPolling = () => {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const restartPolling = () => {
  stopPolling()
  startPolling()
}

watch(runningJob, restartPolling)

watch(() => route.query.job as string | undefined, (v) => {
  if (v && v !== selectedJobId.value) {
    selectedJobId.value = v
    loadDetail()
  }
})

const errorLabel = (key: string | null | undefined): string => {
  if (!key) return ''
  const m = t(`admin.integrity.error.${key}`)
  return m.startsWith('admin.integrity.error.') ? key : m
}

const statusLabel = (status: string): string => {
  const m = t(`admin.integrity.status.${status}`)
  return m.startsWith('admin.integrity.status.') ? status : m
}

const formatDate = (d: string | null | undefined) => d ? new Date(d).toLocaleString() : ''

const itemPathForFailure = (r: IntegrityCheckResult): string | null => {
  // We don't have symlink_path on the failure payload; just link to the admin
  // book detail by hash (admin books view is the natural drill-down).
  return r.hash_id ? `/admin/books?hash=${r.hash_id}` : null
}

// Admin access is enforced by the router's /admin/* beforeEach guard.
onMounted(async () => {
  await loadJobs()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div class="space-y-6">
    <AdminNav />

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <h2 class="text-lg font-semibold mb-4 text-gray-900 dark:text-white">{{ t('admin.integrity.title') }}</h2>

      <div v-if="error" class="mb-4 rounded-lg bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 px-4 py-2 text-sm">
        {{ error }}
      </div>

      <div class="flex flex-wrap gap-3">
        <button
          :disabled="starting || !!runningJob"
          @click="startWholeLibrary('quick')"
          class="px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{ t('admin.integrity.verify_whole_library') }} — {{ t('admin.integrity.quick') }}
        </button>
        <button
          :disabled="starting || !!runningJob"
          @click="startWholeLibrary('full')"
          class="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{ t('admin.integrity.verify_whole_library') }} — {{ t('admin.integrity.full') }}
        </button>
        <div v-if="runningJob" class="self-center text-sm text-gray-500 dark:text-gray-400">
          {{ t('admin.integrity.job_running') }}
        </div>
      </div>
    </section>

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <h2 class="text-lg font-semibold mb-4 text-gray-900 dark:text-white">Jobs</h2>
      <div v-if="!jobs.length" class="text-gray-500 dark:text-gray-400 text-sm">{{ t('admin.integrity.no_active_jobs') }}</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
            <th class="py-2 pr-2 w-44">Started</th>
            <th class="py-2 pr-2 w-24">Mode</th>
            <th class="py-2 pr-2 w-28">Status</th>
            <th class="py-2 pr-2">Progress</th>
            <th class="py-2 pr-2 w-24">OK</th>
            <th class="py-2 pr-2 w-24">Fail</th>
            <th class="py-2 pr-2 w-32"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="j in jobs"
            :key="j.job_id"
            class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/30 cursor-pointer"
            :class="selectedJobId === j.job_id ? 'bg-blue-50 dark:bg-blue-900/20' : ''"
            @click="selectJob(j.job_id)"
          >
            <td class="py-2 pr-2 font-mono text-xs">{{ formatDate(j.started_at) }}</td>
            <td class="py-2 pr-2">{{ t(`admin.integrity.${j.mode}`) }}</td>
            <td class="py-2 pr-2">
              <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium"
                :class="{
                  'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300': j.status === 'running',
                  'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300': j.status === 'done',
                  'bg-gray-100 text-gray-700 dark:bg-gray-900/40 dark:text-gray-300': j.status === 'cancelled',
                  'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300': j.status === 'error',
                }">
                <ArrowPathIcon v-if="j.status === 'running'" class="h-3 w-3 animate-spin" />
                {{ statusLabel(j.status) }}
              </span>
            </td>
            <td class="py-2 pr-2">
              <div class="flex items-center gap-2">
                <div class="flex-1 bg-gray-200 dark:bg-gray-700 rounded h-2 overflow-hidden min-w-[8rem]">
                  <div
                    class="h-full bg-blue-500"
                    :style="{ width: j.total ? `${Math.round(j.processed / j.total * 100)}%` : '0%' }"
                  ></div>
                </div>
                <span class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">{{ j.processed }} / {{ j.total }}</span>
              </div>
            </td>
            <td class="py-2 pr-2 text-emerald-600 dark:text-emerald-400">{{ j.ok_count }}</td>
            <td class="py-2 pr-2 text-red-600 dark:text-red-400">{{ j.fail_count }}</td>
            <td class="py-2 pr-2">
              <button
                v-if="j.status === 'running'"
                @click.stop="cancel(j.job_id)"
                class="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-300 dark:hover:bg-gray-600"
              >
                <NoSymbolIcon class="h-3 w-3" />
                {{ t('admin.integrity.cancel_job') }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="detail" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <h2 class="text-lg font-semibold mb-4 text-gray-900 dark:text-white">
        {{ t('admin.integrity.failures_heading') }}
        <span class="ml-2 text-sm text-gray-500 dark:text-gray-400 font-normal">— {{ detail.fail_count }}</span>
      </h2>

      <div v-if="detail.fail_count === 0" class="text-emerald-600 dark:text-emerald-400 text-sm flex items-center gap-2">
        <CheckCircleIcon class="h-5 w-5" />
        {{ t('admin.integrity.no_failures') }}
      </div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
            <th class="py-2 pr-2">Hash</th>
            <th class="py-2 pr-2">Title</th>
            <th class="py-2 pr-2 w-40">Error</th>
            <th class="py-2 pr-2 w-20"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in detail.failures" :key="r.hash_id" class="border-b border-gray-100 dark:border-gray-700">
            <td class="py-2 pr-2 font-mono text-xs break-all">{{ r.hash_id.slice(0, 16) }}…</td>
            <td class="py-2 pr-2">{{ r.title || r.original_filename || '' }}</td>
            <td class="py-2 pr-2 text-red-600 dark:text-red-400">{{ errorLabel(r.error) }}</td>
            <td class="py-2 pr-2">
              <router-link v-if="itemPathForFailure(r)" :to="itemPathForFailure(r) as string" class="text-blue-600 dark:text-blue-400 hover:underline text-xs">
                open
              </router-link>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="mt-4 flex items-center gap-3">
        <button
          @click="toggleAllResults()"
          class="text-sm text-blue-600 dark:text-blue-400 hover:underline"
        >
          {{ includeAll ? t('admin.integrity.hide_all_results') : t('admin.integrity.show_all_results') }}
        </button>
        <span v-if="detail.all_results_truncated" class="text-xs text-amber-600 dark:text-amber-400">
          {{ t('admin.integrity.all_results_truncated', { cap: 5000 }) }}
        </span>
      </div>

      <div v-if="includeAll && detail.all_results" class="mt-4">
        <h3 class="text-md font-semibold mb-2 text-gray-900 dark:text-white">{{ t('admin.integrity.all_results_heading') }}</h3>
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
              <th class="py-2 pr-2 w-8"></th>
              <th class="py-2 pr-2">Hash</th>
              <th class="py-2 pr-2">Title</th>
              <th class="py-2 pr-2 w-40">Error</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in detail.all_results" :key="r.hash_id" class="border-b border-gray-100 dark:border-gray-700">
              <td class="py-2 pr-2">
                <CheckCircleIcon v-if="r.ok" class="h-4 w-4 text-emerald-500" />
                <XCircleIcon v-else class="h-4 w-4 text-red-500" />
              </td>
              <td class="py-2 pr-2 font-mono text-xs break-all">{{ r.hash_id.slice(0, 16) }}…</td>
              <td class="py-2 pr-2">{{ r.title || r.original_filename || '' }}</td>
              <td class="py-2 pr-2 text-red-600 dark:text-red-400">{{ errorLabel(r.error) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
