<script setup lang="ts">
import { ref, computed, onMounted, inject, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeftIcon, CheckCircleIcon, BookOpenIcon, ArchiveBoxIcon, TrashIcon, LockClosedIcon,
} from '@heroicons/vue/24/outline'
import {
  getFeedbackThread, replyToFeedback, resolveMyFeedback,
  adminSetFeedbackStatus, adminDeleteFeedback,
  type FeedbackThreadDetail, type FeedbackStatus,
} from '../api'
import { refreshFeedbackActiveCount } from '../feedbackBadge'
import FeedbackStatusPill from '../components/feedback/FeedbackStatusPill.vue'
import FeedbackMessageCard from '../components/feedback/FeedbackMessageCard.vue'
import FeedbackReplyBox from '../components/feedback/FeedbackReplyBox.vue'

const props = defineProps<{ publicId: string }>()
const { t } = useI18n()
const router = useRouter()
const currentUser = inject<Ref<{ id?: number; email: string; is_admin?: boolean } | null>>('currentUser')

const detail = ref<FeedbackThreadDetail | null>(null)
const loading = ref(false)
const errorMsg = ref('')

const isAdminViewer = computed(() => !!currentUser?.value?.is_admin)

const STATUS_CYCLE: FeedbackStatus[] = [
  'new', 'open', 'triage', 'progress', 'waiting', 'resolved', 'closed',
]

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const r = await getFeedbackThread(props.publicId)
    detail.value = r.data
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.detail || 'Failed to load thread'
  } finally {
    loading.value = false
  }
  // Refresh the admin badge if the viewer is an admin — covers all the
  // action paths in this view (reply, status change, archive, delete all
  // call load() after their mutation).
  if (isAdminViewer.value) refreshFeedbackActiveCount()
}

async function onSend(payload: { body: string; internal: boolean; new_status: FeedbackStatus | null }) {
  if (!detail.value) return
  await replyToFeedback(detail.value.id, payload.body, {
    internal: payload.internal,
    new_status: payload.new_status ?? undefined,
  })
  await load()
}

async function onMarkResolved() {
  if (!detail.value) return
  await resolveMyFeedback(detail.value.id)
  await load()
}

async function setStatus(s: FeedbackStatus, archive = false) {
  if (!detail.value) return
  await adminSetFeedbackStatus(detail.value.id, s, archive)
  await load()
}

async function archive() {
  if (!detail.value) return
  await adminSetFeedbackStatus(detail.value.id, 'archived', true)
  await load()
}

async function remove() {
  if (!detail.value) return
  if (!confirm(t('admin.feedback.delete_confirm'))) return
  await adminDeleteFeedback(detail.value.id)
  router.push({ name: 'feedback-mine' })
}

// Author-identity is now sourced from the per-message `is_own` flag, which is
// authoritative (the original 'message' row's `is_own` says "the viewer
// wrote the first post"). The old name-vs-email-local-part guess gave
// false positives.
const isOwnThread = computed(() => {
  const first = detail.value?.messages.find(m => m.kind === 'message')
  return !!first?.is_own
})

const canMarkResolved = computed(() =>
  !!detail.value
  && detail.value.status !== 'resolved'
  && detail.value.status !== 'closed'
  && detail.value.status !== 'archived',
)

// For each `kind=status` message, the previous status is whatever the thread
// was in just before that flip — i.e. the body of the last prior status row,
// or 'new' (the thread's initial state) if none exists.
const priorStatusByMsg = computed(() => {
  const m = new Map<number, FeedbackStatus>()
  let current: FeedbackStatus = 'new'
  for (const msg of detail.value?.messages || []) {
    if (msg.kind === 'status') {
      m.set(msg.id, current)
      current = msg.body as FeedbackStatus
    }
  }
  return m
})

onMounted(load)
</script>

<template>
  <div class="px-4 sm:px-6 lg:px-8 py-8">
    <button
      type="button"
      class="inline-flex items-center gap-1 text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white mb-4"
      @click="router.push({ name: 'feedback-mine' })"
    >
      <ArrowLeftIcon class="h-4 w-4" />
      {{ t('feedback.thread_back') }}
    </button>

    <div v-if="loading" class="text-sm text-gray-500 dark:text-gray-400">Loading…</div>
    <div v-else-if="errorMsg" class="rounded-md border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-900/30 px-3 py-2 text-sm text-red-700 dark:text-red-300">
      {{ errorMsg }}
    </div>

    <template v-else-if="detail">
      <div class="flex items-start justify-between gap-3 mb-3">
        <div class="min-w-0">
          <div class="text-[11px] font-mono text-gray-500 dark:text-gray-400">{{ detail.public_id }}</div>
          <h1 class="text-xl md:text-2xl font-bold text-gray-900 dark:text-white break-words">
            {{ detail.subject }}
          </h1>
          <div
            v-if="detail.book_title"
            class="mt-1 inline-flex items-center gap-1 text-xs text-blue-700 dark:text-blue-300"
          >
            <BookOpenIcon class="h-3.5 w-3.5" />
            <router-link
              v-if="detail.book_path"
              :to="`/item/${detail.book_path}`"
              target="_blank"
              class="truncate max-w-md hover:underline"
            >{{ detail.book_title }}</router-link>
            <span v-else class="truncate max-w-md">{{ detail.book_title }}</span>
          </div>
        </div>
        <div class="flex flex-col items-end gap-2 shrink-0">
          <FeedbackStatusPill :status="detail.status" size="md" />
          <div v-if="isAdminViewer" class="flex items-center gap-2">
            <select
              :value="detail.status"
              class="text-xs rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 py-1 px-2"
              @change="setStatus(($event.target as HTMLSelectElement).value as FeedbackStatus)"
            >
              <option v-for="s in STATUS_CYCLE" :key="s" :value="s">{{ t(`feedback.status.${s}`) }}</option>
            </select>
            <button
              type="button"
              :disabled="detail.status === 'archived'"
              :class="[
                'text-xs inline-flex items-center gap-1',
                detail.status === 'archived'
                  ? 'text-gray-300 dark:text-gray-600 cursor-not-allowed'
                  : 'text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white',
              ]"
              :title="t('admin.feedback.archive')"
              @click="archive"
            >
              <ArchiveBoxIcon class="h-4 w-4" />
            </button>
            <button
              type="button"
              class="text-xs text-gray-600 dark:text-gray-300 hover:text-red-500 dark:hover:text-red-400 inline-flex items-center gap-1"
              :title="t('admin.feedback.delete')"
              @click="remove"
            >
              <TrashIcon class="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      <div class="space-y-3 mb-6">
        <FeedbackMessageCard
          v-for="m in detail.messages"
          :key="m.id"
          :message="m"
          :previous-status="priorStatusByMsg.get(m.id) ?? null"
        />
      </div>

      <details v-if="isAdminViewer && detail.diag" class="mb-4">
        <summary class="text-xs text-gray-600 dark:text-gray-400 cursor-pointer inline-flex items-center gap-1">
          <LockClosedIcon class="h-3 w-3" />
          {{ t('admin.feedback.diag_disclosure') }}
        </summary>
        <pre class="mt-2 text-[11px] font-mono bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded p-3 overflow-x-auto text-gray-700 dark:text-gray-300">{{ JSON.stringify(detail.diag, null, 2) }}</pre>
      </details>

      <FeedbackReplyBox
        v-if="detail.status !== 'archived'"
        :as-admin="isAdminViewer"
        :current-status="detail.status"
        @send="onSend"
      />

      <div v-if="canMarkResolved && isOwnThread && !isAdminViewer" class="mt-3 flex justify-end">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 text-sm text-emerald-700 dark:text-emerald-300 hover:underline"
          @click="onMarkResolved"
        >
          <CheckCircleIcon class="h-4 w-4" />
          {{ t('feedback.thread_mark_resolved') }}
        </button>
      </div>
    </template>
  </div>
</template>
