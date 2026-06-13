<script setup lang="ts">
import { ref, computed, onMounted, watch, inject, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  MagnifyingGlassIcon, ArchiveBoxIcon, TrashIcon, UsersIcon, LockClosedIcon,
  ChevronRightIcon,
} from '@heroicons/vue/24/outline'
import AdminNav from '../components/AdminNav.vue'
import FeedbackStatusPill from '../components/feedback/FeedbackStatusPill.vue'
import FeedbackMessageCard from '../components/feedback/FeedbackMessageCard.vue'
import FeedbackReplyBox from '../components/feedback/FeedbackReplyBox.vue'
import {
  adminListFeedback, getFeedbackThread,
  adminSetFeedbackStatus, adminDeleteFeedback,
  replyToFeedback,
  type FeedbackInboxFilter, type FeedbackStatus,
  type FeedbackThreadSummary, type FeedbackThreadDetail,
} from '../api'
import { refreshFeedbackActiveCount } from '../feedbackBadge'

const { t } = useI18n()
const currentUser = inject<Ref<{ id?: number; email: string; is_admin?: boolean } | null>>('currentUser')

const filter = ref<FeedbackInboxFilter>('new')
const search = ref('')
const items = ref<FeedbackThreadSummary[]>([])
const counts = ref<Record<string, number>>({})
const total = ref(0)
const loading = ref(false)

const selectedPublicId = ref<string | null>(null)
const detail = ref<FeedbackThreadDetail | null>(null)
const detailLoading = ref(false)

const FILTERS: FeedbackInboxFilter[] = [
  'mine', 'new', 'open', 'progress', 'waiting', 'resolved', 'archived', 'all',
]

let searchDebounce: ReturnType<typeof setTimeout> | null = null

async function loadList() {
  loading.value = true
  try {
    const r = await adminListFeedback(filter.value, 1, 100, search.value)
    items.value = r.data.items
    counts.value = r.data.counts
    total.value = r.data.total
    if (!selectedPublicId.value && items.value.length > 0) {
      selectedPublicId.value = items.value[0].public_id
      await loadDetail()
    }
    // Keep the AdminNav badge in lockstep with the inbox the admin is looking at.
    refreshFeedbackActiveCount()
  } finally {
    loading.value = false
  }
}

async function loadDetail() {
  if (!selectedPublicId.value) {
    detail.value = null
    return
  }
  detailLoading.value = true
  try {
    const r = await getFeedbackThread(selectedPublicId.value)
    detail.value = r.data
  } catch {
    detail.value = null
  } finally {
    detailLoading.value = false
  }
}

function selectRow(item: FeedbackThreadSummary) {
  selectedPublicId.value = item.public_id
  loadDetail()
}

function onSearch() {
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => loadList(), 250)
}

async function onReply(payload: { body: string; internal: boolean; new_status: FeedbackStatus | null }) {
  if (!detail.value) return
  await replyToFeedback(detail.value.id, payload.body, {
    internal: payload.internal,
    new_status: payload.new_status ?? undefined,
  })
  await loadDetail()
  await loadList()
}

async function setStatus(s: FeedbackStatus, archive = false) {
  if (!detail.value) return
  await adminSetFeedbackStatus(detail.value.id, s, archive)
  await loadDetail()
  await loadList()
}

async function archive() {
  if (!detail.value) return
  await adminSetFeedbackStatus(detail.value.id, 'archived', true)
  await loadDetail()
  await loadList()
}

async function remove() {
  if (!detail.value) return
  if (!confirm(t('admin.feedback.delete_confirm'))) return
  await adminDeleteFeedback(detail.value.id)
  selectedPublicId.value = null
  detail.value = null
  await loadList()
}

watch(filter, () => {
  selectedPublicId.value = null
  detail.value = null
  loadList()
})

onMounted(loadList)

function relTime(iso: string) {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function categoryLabel(item: FeedbackThreadSummary) {
  if (item.category === 'book' && item.book_subcategory) {
    return `${t('feedback.categories.book')} · ${t(`feedback.book_subs.${item.book_subcategory}`)}`
  }
  return t(`feedback.categories.${item.category}`)
}

function isSelfReminder(item: FeedbackThreadSummary): boolean {
  // Self-reminder pattern: the sender is also the (sole) recipient.
  if (item.recipients.length !== 1) return false
  return item.recipients[0].is_you && item.user_email === currentUser?.value?.email
}

const STATUS_CYCLE: FeedbackStatus[] = [
  'new', 'open', 'triage', 'progress', 'waiting', 'resolved', 'closed',
]

// For each `kind=status` message in the open thread, the previous status —
// what the thread was in immediately before that flip.
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
</script>

<template>
  <div class="space-y-6">
    <AdminNav />

    <!-- Sub-tab strip: Inbox / Settings -->
    <div class="flex items-center gap-2 mt-4 mb-4">
      <router-link
        to="/admin/feedback"
        class="px-3 py-1.5 rounded-md text-sm font-medium"
        :class="$route.path === '/admin/feedback' ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
      >{{ t('admin.feedback.inbox_tab') }}</router-link>
      <router-link
        to="/admin/feedback/settings"
        class="px-3 py-1.5 rounded-md text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
      >{{ t('admin.feedback.settings_tab') }}</router-link>
    </div>

    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2 mb-4">
      <button
        v-for="f in FILTERS"
        :key="f"
        type="button"
        :class="[
          'px-2.5 py-1 rounded-full text-xs font-medium border transition inline-flex items-center gap-1',
          filter === f
            ? 'bg-blue-600 text-white border-blue-600'
            : 'bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600',
        ]"
        @click="filter = f"
      >
        {{ f === 'all' ? t('feedback.filter.all') : f === 'mine' ? t('feedback.filter.mine') : t(`feedback.status.${f}`) }}
        <span
          v-if="counts[f] != null && counts[f] > 0"
          :class="[
            'inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full text-[10px] font-bold',
            filter === f ? 'bg-white/20 text-white' : 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200',
          ]"
        >
          {{ counts[f] }}
        </span>
      </button>
      <div class="ml-auto relative max-w-xs w-full">
        <MagnifyingGlassIcon class="h-4 w-4 absolute left-2.5 top-2.5 text-gray-400" />
        <input
          v-model="search"
          type="search"
          placeholder="Search subject / ticket id…"
          class="w-full pl-8 pr-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
          @input="onSearch"
        />
      </div>
    </div>

    <div class="grid grid-cols-12 gap-4">
      <!-- LIST -->
      <div class="col-span-12 lg:col-span-5 xl:col-span-4">
        <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
          <div v-if="loading" class="p-4 text-sm text-gray-500 dark:text-gray-400">Loading…</div>
          <div
            v-else-if="items.length === 0"
            class="p-8 text-center text-sm text-gray-500 dark:text-gray-400"
          >
            {{ t('admin.feedback.empty') }}
          </div>
          <ul v-else class="divide-y divide-gray-200 dark:divide-gray-700 max-h-[70vh] overflow-y-auto">
            <li
              v-for="it in items"
              :key="it.id"
              :class="[
                'cursor-pointer px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700/40',
                selectedPublicId === it.public_id ? 'bg-blue-50 dark:bg-blue-900/30' : '',
              ]"
              @click="selectRow(it)"
            >
              <div class="flex items-center gap-2">
                <span class="text-[10px] font-mono text-gray-500 dark:text-gray-400 truncate max-w-[6rem]">
                  {{ it.public_id }}
                </span>
                <FeedbackStatusPill :status="it.status" />
                <span
                  v-if="!it.is_broadcast"
                  class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-200"
                  :title="it.recipients.map(r => r.name).join(', ')"
                >
                  <UsersIcon class="h-3 w-3" />
                  <template v-if="isSelfReminder(it)">{{ t('admin.feedback.reminder_pill') }}</template>
                  <template v-else>{{ t('admin.feedback.directed_pill') }}: {{ it.recipients.map(r => r.name).join(', ') }}</template>
                </span>
                <span class="ml-auto text-[10px] text-gray-400 dark:text-gray-500 shrink-0">
                  {{ relTime(it.updated_at) }}
                </span>
              </div>
              <div class="flex items-center gap-2 mt-1">
                <span class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate flex-1">
                  {{ it.subject }}
                </span>
                <span v-if="it.has_unread" class="inline-block h-1.5 w-1.5 rounded-full bg-blue-500" title="Unread"></span>
              </div>
              <div class="text-xs text-gray-500 dark:text-gray-400 truncate">
                <span class="font-mono">{{ it.user_email || it.user_name }}</span>
                <template v-if="it.book_title"> · {{ it.book_title }}</template>
                <template v-else> · {{ categoryLabel(it) }}</template>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <!-- READING PANE -->
      <div class="col-span-12 lg:col-span-7 xl:col-span-8">
        <div
          v-if="!detail && !detailLoading"
          class="flex items-center justify-center min-h-[40vh] text-sm text-gray-500 dark:text-gray-400 border border-dashed border-gray-300 dark:border-gray-700 rounded-lg"
        >
          <ChevronRightIcon class="h-5 w-5 mr-1" />
          Select a thread.
        </div>
        <div v-else-if="detailLoading" class="p-4 text-sm text-gray-500 dark:text-gray-400">Loading…</div>
        <div v-else-if="detail" class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 md:p-5">
          <div class="flex items-start justify-between gap-3 mb-3">
            <div class="min-w-0">
              <div class="text-[11px] font-mono text-gray-500 dark:text-gray-400">{{ detail.public_id }}</div>
              <h2 class="text-lg md:text-xl font-bold text-gray-900 dark:text-white break-words">{{ detail.subject }}</h2>
              <div class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                From <span class="font-medium text-gray-700 dark:text-gray-200">{{ detail.user_name }}</span>
                <span class="font-mono"> · {{ detail.user_email }}</span>
              </div>
              <div class="mt-1 text-xs">
                <template v-if="detail.is_broadcast">
                  <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-200">
                    <UsersIcon class="h-3 w-3" />
                    {{ t('admin.feedback.broadcast_pill') }}
                  </span>
                  <span class="ml-1 text-gray-500 dark:text-gray-400">— {{ t('admin.feedback.broadcast_hint') }}</span>
                </template>
                <template v-else>
                  <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-200">
                    <UsersIcon class="h-3 w-3" />
                    {{ t('admin.feedback.directed_pill') }}:
                    {{ detail.recipients.map(r => r.name).join(', ') }}
                  </span>
                </template>
              </div>
            </div>
            <div class="flex flex-col items-end gap-2">
              <FeedbackStatusPill :status="detail.status" size="md" />
              <div class="flex items-center gap-2">
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

          <div
            v-if="detail.book_title"
            class="mb-3 inline-flex max-w-full items-center gap-1.5 px-2 py-1 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-800 dark:text-blue-200 text-xs"
          >
            <span class="font-medium shrink-0">Book:</span>
            <router-link
              v-if="detail.book_path"
              :to="`/item/${detail.book_path}`"
              target="_blank"
              class="truncate hover:underline"
            >{{ detail.book_title }}</router-link>
            <span v-else class="truncate">{{ detail.book_title }}</span>
          </div>

          <div class="space-y-3 mb-6">
            <FeedbackMessageCard
              v-for="m in detail.messages"
              :key="m.id"
              :message="m"
              :previous-status="priorStatusByMsg.get(m.id) ?? null"
            />
          </div>

          <details v-if="detail.diag" class="mb-4">
            <summary class="text-xs text-gray-600 dark:text-gray-400 cursor-pointer inline-flex items-center gap-1">
              <LockClosedIcon class="h-3 w-3" />
              {{ t('admin.feedback.diag_disclosure') }}
            </summary>
            <pre class="mt-2 text-[11px] font-mono bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded p-3 overflow-x-auto text-gray-700 dark:text-gray-300">{{ JSON.stringify(detail.diag, null, 2) }}</pre>
          </details>

          <FeedbackReplyBox
            v-if="detail.status !== 'archived'"
            as-admin
            :current-status="detail.status"
            @send="onReply"
          />
        </div>
      </div>
    </div>
  </div>
</template>
