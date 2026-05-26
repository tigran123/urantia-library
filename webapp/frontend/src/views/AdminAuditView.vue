<script setup lang="ts">
import { ref, computed, onMounted, inject, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api from '../api'
import AdminNav from '../components/AdminNav.vue'
import { userInitials } from '../userDisplay'
import {
  adminAuditFeed, adminAuditStats,
  type AuditLogEntry, type AuditLeaderEntry,
} from '../api'

const { t } = useI18n({ useScope: 'global' })

type CurrentUser = { email: string, is_admin?: boolean } | null
const currentUser = inject<{ value: CurrentUser } | null>('currentUser', null)
const router = useRouter()

// ----- Mode toggle (Timeline vs Leaderboard) -----
type Mode = 'timeline' | 'leaderboard'
const mode = ref<Mode>('timeline')

// ----- Date-range filter (shared by both modes) -----
type Range = '24h' | '7d' | '30d' | 'all'
const range = ref<Range>('30d')

const sinceIso = computed((): string | null => {
  if (range.value === 'all') return null
  const ms = range.value === '24h' ? 86_400_000
           : range.value === '7d'  ? 7  * 86_400_000
           : /* 30d */               30 * 86_400_000
  return new Date(Date.now() - ms).toISOString()
})

// ----- Action filter (timeline only). Empty string = no filter. -----
const ACTION_OPTIONS: { value: string, key: string }[] = [
  { value: '',                     key: 'admin.audit.action.all' },
  { value: 'book.upload',          key: 'admin.audit.action.book_upload' },
  { value: 'book.edit',            key: 'admin.audit.action.book_edit' },
  { value: 'book.delete',          key: 'admin.audit.action.book_delete' },
  { value: 'book.move',            key: 'admin.audit.action.book_move' },
  { value: 'book.cover',           key: 'admin.audit.action.book_cover' },
  { value: 'book.clearance',       key: 'admin.audit.action.book_clearance' },
  { value: 'user.clearance',       key: 'admin.audit.action.user_clearance' },
  { value: 'user.session_terminate', key: 'admin.audit.action.user_session_terminate' },
  { value: 'comment.moderate',     key: 'admin.audit.action.comment_moderate' },
  { value: 'annotation.moderate',  key: 'admin.audit.action.annotation_moderate' },
]
const actionFilter = ref('')
const actorFilter = ref<number | null>(null)   // set by clicking a leaderboard row

// ----- Action chip styling. Tailwind needs the full class string at build time
// so we can't generate them — these are looked up by exact action key. -----
const ACTION_CHIP_CLASSES: Record<string, string> = {
  'book.upload':            'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  'book.edit':              'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  'book.delete':            'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  'book.move':              'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
  'book.cover':             'bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300',
  'book.clearance':         'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  'user.clearance':         'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  'user.session_terminate': 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
  'comment.moderate':       'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  'annotation.moderate':    'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300',
}
const chipClass = (action: string) =>
  ACTION_CHIP_CLASSES[action] || 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300'

// More saturated variants of the same hues for the leaderboard chart's bar
// segments — the pale `-100` shades wash out into the page background.
const ACTION_SEGMENT_CLASSES: Record<string, string> = {
  'book.upload':            'bg-emerald-500 dark:bg-emerald-400',
  'book.edit':              'bg-blue-500 dark:bg-blue-400',
  'book.delete':            'bg-red-500 dark:bg-red-400',
  'book.move':              'bg-violet-500 dark:bg-violet-400',
  'book.cover':             'bg-pink-500 dark:bg-pink-400',
  'book.clearance':         'bg-amber-500 dark:bg-amber-400',
  'user.clearance':         'bg-indigo-500 dark:bg-indigo-400',
  'user.session_terminate': 'bg-orange-500 dark:bg-orange-400',
  'comment.moderate':       'bg-teal-500 dark:bg-teal-400',
  'annotation.moderate':    'bg-cyan-500 dark:bg-cyan-400',
}
const segmentClass = (action: string) =>
  ACTION_SEGMENT_CLASSES[action] || 'bg-gray-400 dark:bg-gray-500'

// ----- Timeline state -----
const entries = ref<AuditLogEntry[]>([])
const nextCursor = ref<number | null>(null)
const loadingFeed = ref(false)
const loadingMore = ref(false)
const expanded = ref<Set<number>>(new Set())
const feedError = ref('')

const loadFeed = async () => {
  loadingFeed.value = true
  feedError.value = ''
  try {
    const res = await adminAuditFeed({
      limit: 50,
      since: sinceIso.value,
      action: actionFilter.value || null,
      actor_user_id: actorFilter.value,
    })
    entries.value = res.data.entries
    nextCursor.value = res.data.next_cursor
  } catch (err: any) {
    feedError.value = err.response?.data?.detail || err.message
  } finally {
    loadingFeed.value = false
  }
}

const loadMore = async () => {
  if (nextCursor.value === null) return
  loadingMore.value = true
  try {
    const res = await adminAuditFeed({
      limit: 50,
      cursor: nextCursor.value,
      since: sinceIso.value,
      action: actionFilter.value || null,
      actor_user_id: actorFilter.value,
    })
    entries.value = [...entries.value, ...res.data.entries]
    nextCursor.value = res.data.next_cursor
  } catch (err: any) {
    feedError.value = err.response?.data?.detail || err.message
  } finally {
    loadingMore.value = false
  }
}

// ----- Leaderboard state -----
const leaders = ref<AuditLeaderEntry[]>([])
const loadingLeaders = ref(false)
const leadersError = ref('')
// Union of action keys actually seen across leaders, in our canonical order.
// Used to build the per-action columns. Empty actions (everyone's zero) are skipped.
const CANONICAL_ACTIONS = ACTION_OPTIONS.slice(1).map(o => o.value)
const activeActionKeys = computed(() =>
  CANONICAL_ACTIONS.filter(a => leaders.value.some(L => (L.totals[a] || 0) > 0))
)
// Max total across admins — the leader's bar fills the chart width, everyone
// else is proportional. `|| 1` keeps the math safe when leaders is empty.
const topTotal = computed(() =>
  leaders.value.reduce((m, L) => Math.max(m, L.total), 0) || 1
)

const loadLeaders = async () => {
  loadingLeaders.value = true
  leadersError.value = ''
  try {
    const res = await adminAuditStats(sinceIso.value)
    leaders.value = res.data.leaders
  } catch (err: any) {
    leadersError.value = err.response?.data?.detail || err.message
  } finally {
    loadingLeaders.value = false
  }
}

// ----- Reactive reloads -----
// A single combined watch so that drillIntoActor (which mutates both mode and
// actorFilter in the same tick) triggers exactly one reload, not two racing
// ones. Vue batches the dependency changes within a tick and fires once.
watch([mode, range, actionFilter, actorFilter], () => {
  if (mode.value === 'timeline') loadFeed()
  else loadLeaders()
})

// Click an actor row in the leaderboard → jump to timeline filtered by them.
const drillIntoActor = (actorId: number) => {
  actorFilter.value = actorId
  mode.value = 'timeline'
}
const clearActorFilter = () => { actorFilter.value = null }

// ----- Display helpers -----
const formatDate = (s: string) => (s ? new Date(s).toLocaleString() : '')

const relativeTime = (s: string): string => {
  const then = new Date(s).getTime()
  if (Number.isNaN(then)) return s
  const sec = Math.max(0, (Date.now() - then) / 1000)
  if (sec < 60) return t('admin.audit.rel.just_now')
  if (sec < 3600) return t('admin.audit.rel.minutes', { n: Math.floor(sec / 60) })
  if (sec < 86400) return t('admin.audit.rel.hours', { n: Math.floor(sec / 3600) })
  if (sec < 7 * 86400) return t('admin.audit.rel.days', { n: Math.floor(sec / 86400) })
  return formatDate(s)
}

const actorDisplay = (e: { actor: { real_name: string | null, email: string } }) =>
  e.actor.real_name || e.actor.email

const actionLabel = (action: string): string => {
  const opt = ACTION_OPTIONS.find(o => o.value === action)
  return opt ? t(opt.key) : action
}

const toggleExpand = (id: number) => {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
  // Force re-render (Set isn't reactive over membership).
  expanded.value = new Set(expanded.value)
}

const getFullUrl = (url: string | null): string => {
  if (!url) return ''
  return (api.defaults.baseURL?.replace('/api', '') || '') + url
}

const prettyDetails = (d: Record<string, unknown> | null): string => {
  if (!d) return ''
  try { return JSON.stringify(d, null, 2) } catch { return String(d) }
}

// ----- Auth gate (same pattern as the other admin views) -----
const gate = (u: CurrentUser) => {
  if (!u) return
  if (u.is_admin) {
    loadFeed()
  } else {
    router.replace('/')
  }
}

onMounted(() => {
  if (currentUser?.value) {
    gate(currentUser.value)
  } else {
    const stop = watch(() => currentUser?.value, (u) => {
      if (u) { stop(); gate(u) }
    })
  }
})
</script>

<template>
  <div class="space-y-6 max-w-6xl mx-auto">
    <AdminNav />

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <!-- Mode toggle + date range pills -->
      <div class="flex items-center justify-between mb-4 gap-4 flex-wrap">
        <div class="flex gap-1 p-1 rounded-md bg-gray-100 dark:bg-gray-700">
          <button
            @click="mode = 'timeline'"
            class="px-3 py-1 rounded text-sm font-medium"
            :class="mode === 'timeline'
              ? 'bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-sm'
              : 'text-gray-700 dark:text-gray-300'"
          >{{ t('admin.audit.mode_timeline') }}</button>
          <button
            @click="mode = 'leaderboard'"
            class="px-3 py-1 rounded text-sm font-medium"
            :class="mode === 'leaderboard'
              ? 'bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-sm'
              : 'text-gray-700 dark:text-gray-300'"
          >{{ t('admin.audit.mode_leaderboard') }}</button>
        </div>

        <div class="flex gap-1">
          <button
            v-for="r in (['24h','7d','30d','all'] as const)" :key="r"
            @click="range = r"
            class="px-2.5 py-1 rounded text-xs font-medium"
            :class="range === r
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300'"
          >{{ t(`admin.audit.range.${r}`) }}</button>
        </div>
      </div>

      <!-- ============================ TIMELINE ============================ -->
      <template v-if="mode === 'timeline'">
        <!-- Action filter + active actor filter chip -->
        <div class="flex items-center gap-3 mb-4 flex-wrap text-sm">
          <label class="flex items-center gap-2">
            <span class="text-gray-600 dark:text-gray-400">{{ t('admin.audit.filter_action') }}:</span>
            <select
              v-model="actionFilter"
              class="border border-gray-300 dark:border-gray-600 rounded px-2 py-1 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
            >
              <option v-for="opt in ACTION_OPTIONS" :key="opt.value" :value="opt.value">
                {{ t(opt.key) }}
              </option>
            </select>
          </label>
          <button
            v-if="actorFilter !== null"
            @click="clearActorFilter"
            class="inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-900/60"
            :title="t('admin.audit.clear_actor_filter')"
          >
            {{ t('admin.audit.filtered_by_actor') }}
            <span aria-hidden="true">×</span>
          </button>
        </div>

        <div v-if="loadingFeed" class="text-gray-500 dark:text-gray-400">{{ t('admin.loading') }}</div>
        <div v-else-if="feedError" class="text-red-600 dark:text-red-400">{{ feedError }}</div>
        <p v-else-if="!entries.length" class="text-gray-500 dark:text-gray-400">{{ t('admin.audit.empty') }}</p>
        <template v-else>
          <ul class="divide-y divide-gray-100 dark:divide-gray-700">
            <li v-for="e in entries" :key="e.id" class="py-3">
              <div class="flex items-start gap-3">
                <!-- avatar -->
                <img
                  v-if="e.actor.avatar_url"
                  :src="getFullUrl(e.actor.avatar_url)"
                  class="h-8 w-8 rounded-full object-cover border border-gray-200 dark:border-gray-700 flex-shrink-0"
                  alt=""
                />
                <span
                  v-else
                  class="h-8 w-8 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-semibold text-gray-600 dark:text-gray-200 flex-shrink-0"
                >{{ userInitials(e.actor) }}</span>

                <div class="min-w-0 flex-1">
                  <div class="flex items-center gap-2 flex-wrap text-xs text-gray-500 dark:text-gray-400">
                    <span class="font-semibold text-gray-900 dark:text-gray-100">{{ actorDisplay(e) }}</span>
                    <span
                      class="px-1.5 py-0.5 rounded font-medium"
                      :class="chipClass(e.action)"
                    >{{ actionLabel(e.action) }}</span>
                    <span :title="formatDate(e.created_at)">{{ relativeTime(e.created_at) }}</span>
                  </div>
                  <p class="mt-0.5 text-sm text-gray-800 dark:text-gray-200">{{ e.summary }}</p>
                  <button
                    v-if="e.details"
                    @click="toggleExpand(e.id)"
                    class="mt-1 text-xs text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    {{ expanded.has(e.id) ? t('admin.audit.hide_details') : t('admin.audit.show_details') }}
                  </button>
                  <pre
                    v-if="e.details && expanded.has(e.id)"
                    class="mt-2 p-2 rounded bg-gray-50 dark:bg-gray-900 text-xs text-gray-700 dark:text-gray-300 overflow-x-auto"
                  >{{ prettyDetails(e.details) }}</pre>
                </div>
              </div>
            </li>
          </ul>

          <div class="mt-4 flex items-center justify-center gap-4 text-sm text-gray-500 dark:text-gray-400">
            <button
              v-if="nextCursor !== null"
              @click="loadMore"
              :disabled="loadingMore"
              class="px-4 py-1.5 rounded-md font-medium bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50"
            >{{ loadingMore ? t('admin.loading') : t('admin.audit.load_more') }}</button>
            <span v-else>{{ t('admin.audit.end_of_feed') }}</span>
          </div>
        </template>
      </template>

      <!-- ============================ LEADERBOARD ============================ -->
      <template v-else>
        <div v-if="loadingLeaders" class="text-gray-500 dark:text-gray-400">{{ t('admin.loading') }}</div>
        <div v-else-if="leadersError" class="text-red-600 dark:text-red-400">{{ leadersError }}</div>
        <p v-else-if="!leaders.length" class="text-gray-500 dark:text-gray-400">{{ t('admin.audit.empty') }}</p>
        <template v-else>
          <!-- Stacked horizontal bar chart. One row per admin, sorted by total
               desc (matches the table order below). Each row's bar width is
               proportional to that admin's share of the leader's total; the
               segments within are proportional to per-action counts. -->
          <div class="mb-5 space-y-2">
            <div
              v-for="L in leaders" :key="`bar-${L.actor.id}`"
              @click="drillIntoActor(L.actor.id)"
              class="flex items-center gap-3 cursor-pointer group"
              :title="t('admin.audit.click_to_filter')"
            >
              <div class="w-48 flex items-center gap-2 flex-shrink-0">
                <img
                  v-if="L.actor.avatar_url"
                  :src="getFullUrl(L.actor.avatar_url)"
                  class="h-7 w-7 rounded-full object-cover border border-gray-200 dark:border-gray-700"
                  alt=""
                />
                <span
                  v-else
                  class="h-7 w-7 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-semibold text-gray-600 dark:text-gray-200"
                >{{ userInitials(L.actor) }}</span>
                <span class="truncate text-sm text-gray-900 dark:text-gray-100 group-hover:text-blue-600 dark:group-hover:text-blue-400">
                  {{ L.actor.real_name || L.actor.email }}
                </span>
              </div>
              <div class="flex-1 min-w-0">
                <div
                  class="flex h-6 rounded overflow-hidden ring-1 ring-gray-200 dark:ring-gray-700"
                  :style="{ width: (L.total / topTotal * 100) + '%' }"
                >
                  <template v-for="a in activeActionKeys" :key="a">
                    <div
                      v-if="L.totals[a]"
                      :class="segmentClass(a)"
                      :style="{ flex: L.totals[a] }"
                      :title="`${actionLabel(a)}: ${L.totals[a]}`"
                    ></div>
                  </template>
                </div>
              </div>
              <div class="w-10 text-right text-sm tabular-nums font-semibold text-gray-900 dark:text-gray-100 flex-shrink-0">
                {{ L.total }}
              </div>
            </div>
          </div>

          <!-- Legend — only the actions that actually appear in the chart. -->
          <div class="flex flex-wrap gap-x-4 gap-y-2 mb-6 text-xs">
            <div v-for="a in activeActionKeys" :key="`leg-${a}`" class="flex items-center gap-1.5">
              <span :class="['inline-block w-3 h-3 rounded-sm', segmentClass(a)]"></span>
              <span class="text-gray-700 dark:text-gray-300">{{ actionLabel(a) }}</span>
            </div>
          </div>

        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs text-gray-500 dark:text-gray-400 uppercase border-b border-gray-200 dark:border-gray-700">
                <th class="py-2 pr-3">{{ t('admin.audit.leader.actor') }}</th>
                <th class="py-2 pr-3 text-right">{{ t('admin.audit.leader.total') }}</th>
                <th v-for="a in activeActionKeys" :key="a" class="py-2 pr-3 text-right">
                  <span :class="['px-1.5 py-0.5 rounded font-medium normal-case', chipClass(a)]">
                    {{ actionLabel(a) }}
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="L in leaders" :key="L.actor.id"
                class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/30 cursor-pointer"
                @click="drillIntoActor(L.actor.id)"
                :title="t('admin.audit.click_to_filter')"
              >
                <td class="py-2 pr-3">
                  <div class="flex items-center gap-3">
                    <img
                      v-if="L.actor.avatar_url"
                      :src="getFullUrl(L.actor.avatar_url)"
                      class="h-8 w-8 rounded-full object-cover border border-gray-200 dark:border-gray-700 flex-shrink-0"
                      alt=""
                    />
                    <span
                      v-else
                      class="h-8 w-8 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-semibold text-gray-600 dark:text-gray-200 flex-shrink-0"
                    >{{ userInitials(L.actor) }}</span>
                    <div class="min-w-0">
                      <div v-if="L.actor.real_name" class="truncate text-gray-900 dark:text-gray-100">{{ L.actor.real_name }}</div>
                      <div class="truncate text-gray-500 dark:text-gray-400" :class="L.actor.real_name ? 'text-xs' : ''">{{ L.actor.email }}</div>
                    </div>
                  </div>
                </td>
                <td class="py-2 pr-3 text-right font-semibold text-gray-900 dark:text-gray-100 tabular-nums">{{ L.total }}</td>
                <td v-for="a in activeActionKeys" :key="a" class="py-2 pr-3 text-right tabular-nums text-gray-700 dark:text-gray-300">
                  {{ L.totals[a] || '' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        </template>
      </template>
    </section>
  </div>
</template>
