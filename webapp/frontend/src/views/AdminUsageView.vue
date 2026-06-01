<script setup lang="ts">
import { ref, computed, onMounted, watch, inject, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api'
import AdminNav from '../components/AdminNav.vue'

const { t } = useI18n({ useScope: 'global' })

// Localised label for a usage-event kind (page, book_open, recommend, …).
// Falls back to the raw key when a translation is missing.
const kindLabel = (kind: string): string => {
  const key = `admin.usage.kind.${kind}`
  const tx = t(key)
  return tx === key ? kind : tx
}
const currentUser = inject<Ref<{ search_per_page?: number | null } | null>>(
  'currentUser',
  ref(null) as unknown as Ref<{ search_per_page?: number | null } | null>,
)

// Re-fetch the active sub-tab when the operator edits Settings → Results
// per page; the backend timeline endpoint defaults `per_page` to
// users.search_per_page, so we just retrigger after the modal saves.
watch(() => currentUser.value?.search_per_page, () => loadActive())

type Tab = 'overview' | 'geography' | 'books' | 'users' | 'ips' | 'timeline' | 'settings'
const tab = ref<Tab>('overview')

type Range = '7' | '30' | '90' | '365'
const days = ref<Range>('30')

const loading = ref(false)
const errorMsg = ref('')

// Overview ---------------------------------------------------------------
interface Overview {
  since: string
  days: number
  total_events: number
  by_kind: Record<string, number>
  unique_users: number
  unique_ips: number
  unique_countries: number
  daily: { date: string, count: number }[]
}
const overview = ref<Overview | null>(null)

// Geography --------------------------------------------------------------
interface CountryRow { country: string, events: number, unique_ips: number, unique_users: number }
interface CityRow    { city: string, events: number, unique_ips: number }
const countries = ref<CountryRow[]>([])
const selectedCountry = ref<string | null>(null)
const cities = ref<CityRow[]>([])

// Books ------------------------------------------------------------------
interface BookRow { hash_id: string, title: string | null, author: string | null, opens: number, signed_in_readers: number, guest_ips: number }
const books = ref<BookRow[]>([])

// Users ------------------------------------------------------------------
interface UserRow { user_id: number, email: string, real_name: string | null, total_events: number, unique_books_opened: number, last_seen: string }
const users = ref<UserRow[]>([])

// IPs --------------------------------------------------------------------
interface IpRow { ip: string, country: string | null, city: string | null, events: number, unique_users: number, first_seen: string, last_seen: string }
const ips = ref<IpRow[]>([])

// Timeline ---------------------------------------------------------------
interface TimelineRow {
  id: number, ts: string, user_id: number | null, user_email: string | null,
  session_jti: string | null, ip: string, user_agent: string | null,
  geo_country: string | null, geo_city: string | null, kind: string,
  path: string | null, hash_id: string | null, extra: Record<string, any> | null,
}
const timeline = ref<TimelineRow[]>([])
const timelinePage = ref(1)
const timelineTotalPages = ref(0)
const timelineTotal = ref(0)
const timelineFilters = ref({
  kind: '',
  user: '',         // email or numeric id; backend disambiguates
  ip: '',
  hash_id: '',
  country: '',
})

// Settings ---------------------------------------------------------------
interface UsageSettings { enabled_kinds: string[], all_kinds: string[] }
const settings = ref<UsageSettings | null>(null)
const settingsLocalEnabled = ref<Record<string, boolean>>({})
const settingsSaving = ref(false)
const settingsSavedAt = ref<number | null>(null)

// Data loaders -----------------------------------------------------------
async function loadOverview() {
  loading.value = true; errorMsg.value = ''
  try {
    const { data } = await api.get<Overview>('/admin/usage/overview', { params: { days: days.value } })
    overview.value = data
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error loading overview'
  } finally { loading.value = false }
}

async function loadCountries() {
  loading.value = true; errorMsg.value = ''
  try {
    const { data } = await api.get<{ countries: CountryRow[] }>('/admin/usage/by-country', { params: { days: days.value } })
    countries.value = data.countries
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error'
  } finally { loading.value = false }
}

async function loadCities(country: string) {
  selectedCountry.value = country
  loading.value = true; errorMsg.value = ''
  try {
    const { data } = await api.get<{ cities: CityRow[] }>('/admin/usage/by-city', { params: { days: days.value, country } })
    cities.value = data.cities
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error'
  } finally { loading.value = false }
}

async function loadBooks() {
  loading.value = true; errorMsg.value = ''
  try {
    const { data } = await api.get<{ books: BookRow[] }>('/admin/usage/by-book', { params: { days: days.value, limit: 100 } })
    books.value = data.books
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error'
  } finally { loading.value = false }
}

async function loadUsers() {
  loading.value = true; errorMsg.value = ''
  try {
    const { data } = await api.get<{ users: UserRow[] }>('/admin/usage/by-user', { params: { days: days.value, limit: 200 } })
    users.value = data.users
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error'
  } finally { loading.value = false }
}

async function loadIps() {
  loading.value = true; errorMsg.value = ''
  try {
    const { data } = await api.get<{ ips: IpRow[] }>('/admin/usage/by-ip', { params: { days: days.value, limit: 200 } })
    ips.value = data.ips
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error'
  } finally { loading.value = false }
}

async function loadTimeline(p: number = 1) {
  loading.value = true; errorMsg.value = ''
  try {
    // per_page is server-controlled via the admin's users.search_per_page
    // setting, mirroring the Search results page; we just pass the page number.
    const params: Record<string, any> = { page: p }
    for (const k of Object.keys(timelineFilters.value) as Array<keyof typeof timelineFilters.value>) {
      const v = timelineFilters.value[k]
      if (v) params[k] = v
    }
    const { data } = await api.get<{ entries: TimelineRow[], page: number, total: number, total_pages: number }>(
      '/admin/usage/timeline', { params }
    )
    timeline.value = data.entries
    timelinePage.value = data.page
    timelineTotal.value = data.total
    timelineTotalPages.value = data.total_pages
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error'
  } finally { loading.value = false }
}

async function loadSettings() {
  loading.value = true; errorMsg.value = ''
  try {
    const { data } = await api.get<UsageSettings>('/admin/usage/settings')
    settings.value = data
    settingsLocalEnabled.value = Object.fromEntries(
      data.all_kinds.map(k => [k, data.enabled_kinds.includes(k)])
    )
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error'
  } finally { loading.value = false }
}

async function saveSettings() {
  settingsSaving.value = true; errorMsg.value = ''
  try {
    const enabled = Object.entries(settingsLocalEnabled.value)
      .filter(([, on]) => on).map(([k]) => k)
    const { data } = await api.put<UsageSettings>('/admin/usage/settings', { enabled_kinds: enabled })
    settings.value = data
    settingsSavedAt.value = Date.now()
  } catch (err: any) {
    errorMsg.value = err?.response?.data?.detail || 'Error'
  } finally { settingsSaving.value = false }
}

function loadActive() {
  if (tab.value === 'overview') loadOverview()
  else if (tab.value === 'geography') { loadCountries(); selectedCountry.value = null; cities.value = [] }
  else if (tab.value === 'books') loadBooks()
  else if (tab.value === 'users') loadUsers()
  else if (tab.value === 'ips') loadIps()
  else if (tab.value === 'timeline') loadTimeline(1)
  else if (tab.value === 'settings') loadSettings()
}

watch([tab, days], loadActive)
onMounted(loadActive)

// Compute max for sparkline ---------------------------------------------
const sparkMax = computed(() => Math.max(1, ...(overview.value?.daily.map(d => d.count) || [0])))

function formatExtra(extra: Record<string, any> | null): string {
  if (!extra) return ''
  try { return JSON.stringify(extra) } catch { return '' }
}
</script>

<template>
  <div class="px-4 py-4">
    <AdminNav />

    <div class="flex items-center justify-between flex-wrap gap-3 mb-4 mt-3">
      <div class="flex items-center gap-1">
        <button
          v-for="tabKey in ['overview', 'geography', 'books', 'users', 'ips', 'timeline', 'settings'] as Tab[]"
          :key="tabKey"
          @click="tab = tabKey"
          :class="[
            'px-3 py-1.5 rounded-md text-sm font-medium',
            tab === tabKey ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
          ]"
        >
          {{ t('admin.usage.subtab.' + tabKey) }}
        </button>
      </div>

      <div class="flex items-center gap-2">
        <label class="text-sm text-gray-600 dark:text-gray-400">{{ t('admin.usage.rangeLabel') }}</label>
        <select v-model="days" class="text-sm px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800">
          <option value="7">{{ t('admin.usage.range.7d') }}</option>
          <option value="30">{{ t('admin.usage.range.30d') }}</option>
          <option value="90">{{ t('admin.usage.range.90d') }}</option>
          <option value="365">{{ t('admin.usage.range.365d') }}</option>
        </select>
      </div>
    </div>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800 rounded text-sm">
      {{ errorMsg }}
    </div>

    <div v-if="loading" class="text-sm text-gray-500 italic">{{ t('admin.usage.loading') }}</div>

    <!-- Overview --------------------------------------------------------- -->
    <div v-if="tab === 'overview' && overview && !loading">
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="border border-gray-200 dark:border-gray-700 rounded p-4">
          <div class="text-xs text-gray-500">{{ t('admin.usage.stat.totalEvents') }}</div>
          <div class="text-2xl font-bold">{{ overview.total_events.toLocaleString() }}</div>
        </div>
        <div class="border border-gray-200 dark:border-gray-700 rounded p-4">
          <div class="text-xs text-gray-500">{{ t('admin.usage.stat.uniqueUsers') }}</div>
          <div class="text-2xl font-bold">{{ overview.unique_users }}</div>
        </div>
        <div class="border border-gray-200 dark:border-gray-700 rounded p-4">
          <div class="text-xs text-gray-500">{{ t('admin.usage.stat.uniqueIps') }}</div>
          <div class="text-2xl font-bold">{{ overview.unique_ips }}</div>
        </div>
        <div class="border border-gray-200 dark:border-gray-700 rounded p-4">
          <div class="text-xs text-gray-500">{{ t('admin.usage.stat.uniqueCountries') }}</div>
          <div class="text-2xl font-bold">{{ overview.unique_countries }}</div>
        </div>
      </div>

      <div class="border border-gray-200 dark:border-gray-700 rounded p-4 mb-6">
        <h3 class="text-sm font-medium mb-3">{{ t('admin.usage.byKindTitle') }}</h3>
        <div class="space-y-1 text-sm">
          <div v-for="(n, kind) in overview.by_kind" :key="kind" class="flex items-center gap-2">
            <span class="inline-block w-32 text-gray-600 dark:text-gray-400">{{ kindLabel(kind) }}</span>
            <span class="font-mono">{{ n }}</span>
          </div>
        </div>
      </div>

      <div class="border border-gray-200 dark:border-gray-700 rounded p-4">
        <h3 class="text-sm font-medium mb-3">{{ t('admin.usage.dailyTitle') }}</h3>
        <div class="flex items-end gap-0.5 h-32 border-b border-gray-200 dark:border-gray-700">
          <div
            v-for="d in overview.daily"
            :key="d.date"
            class="flex-1 bg-blue-500 dark:bg-blue-400 min-w-[2px]"
            :style="{ height: `${(d.count / sparkMax) * 100}%` }"
            :title="`${d.date}: ${d.count}`"
          />
        </div>
        <div class="flex justify-between text-xs text-gray-500 mt-1">
          <span>{{ overview.daily[0]?.date }}</span>
          <span>{{ overview.daily[overview.daily.length - 1]?.date }}</span>
        </div>
      </div>
    </div>

    <!-- Geography -------------------------------------------------------- -->
    <div v-if="tab === 'geography' && !loading" class="grid md:grid-cols-2 gap-4">
      <div class="border border-gray-200 dark:border-gray-700 rounded overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="bg-gray-50 dark:bg-gray-800 text-left">
            <tr>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.country') }}</th>
              <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.events') }}</th>
              <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.ips') }}</th>
              <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.users') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in countries" :key="row.country"
              :class="['border-t border-gray-100 dark:border-gray-700 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800', selectedCountry === row.country ? 'bg-blue-50 dark:bg-blue-900/20' : '']"
              @click="loadCities(row.country)"
            >
              <td class="px-3 py-2 font-mono">{{ row.country }}</td>
              <td class="px-3 py-2 text-right font-mono">{{ row.events }}</td>
              <td class="px-3 py-2 text-right font-mono">{{ row.unique_ips }}</td>
              <td class="px-3 py-2 text-right font-mono">{{ row.unique_users }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="selectedCountry" class="border border-gray-200 dark:border-gray-700 rounded overflow-x-auto">
        <div class="px-3 py-2 bg-gray-50 dark:bg-gray-800 text-sm font-medium border-b border-gray-200 dark:border-gray-700">
          {{ t('admin.usage.citiesIn', { country: selectedCountry }) }}
        </div>
        <table class="w-full text-sm">
          <thead class="bg-gray-50 dark:bg-gray-800 text-left">
            <tr>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.city') }}</th>
              <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.events') }}</th>
              <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.ips') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in cities" :key="row.city" class="border-t border-gray-100 dark:border-gray-700">
              <td class="px-3 py-2">{{ row.city }}</td>
              <td class="px-3 py-2 text-right font-mono">{{ row.events }}</td>
              <td class="px-3 py-2 text-right font-mono">{{ row.unique_ips }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Books ------------------------------------------------------------ -->
    <div v-if="tab === 'books' && !loading" class="border border-gray-200 dark:border-gray-700 rounded overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-gray-50 dark:bg-gray-800 text-left">
          <tr>
            <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.title') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.author') }}</th>
            <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.opens') }}</th>
            <th class="px-3 py-2 font-medium text-right" :title="t('admin.usage.col.signedInReadersHint')">{{ t('admin.usage.col.signedInReaders') }}</th>
            <th class="px-3 py-2 font-medium text-right" :title="t('admin.usage.col.guestsHint')">{{ t('admin.usage.col.guests') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in books" :key="row.hash_id" class="border-t border-gray-100 dark:border-gray-700">
            <td class="px-3 py-2 max-w-md truncate" :title="row.title || ''">{{ row.title || '(no title)' }}</td>
            <td class="px-3 py-2 max-w-xs truncate text-gray-600 dark:text-gray-400">{{ row.author }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ row.opens }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ row.signed_in_readers }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ row.guest_ips }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Users ------------------------------------------------------------ -->
    <div v-if="tab === 'users' && !loading" class="border border-gray-200 dark:border-gray-700 rounded overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-gray-50 dark:bg-gray-800 text-left">
          <tr>
            <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.user') }}</th>
            <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.events') }}</th>
            <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.booksOpened') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.lastSeen') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in users" :key="row.user_id" class="border-t border-gray-100 dark:border-gray-700">
            <td class="px-3 py-2">
              <div>
                <span class="text-gray-400 dark:text-gray-500 font-mono mr-1 text-xs">{{ row.user_id }} ·</span>
                {{ row.real_name || row.email }}
              </div>
              <div v-if="row.real_name" class="text-xs text-gray-500">{{ row.email }}</div>
            </td>
            <td class="px-3 py-2 text-right font-mono">{{ row.total_events }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ row.unique_books_opened }}</td>
            <td class="px-3 py-2 font-mono text-xs">{{ row.last_seen }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- IPs -------------------------------------------------------------- -->
    <div v-if="tab === 'ips' && !loading" class="border border-gray-200 dark:border-gray-700 rounded overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-gray-50 dark:bg-gray-800 text-left">
          <tr>
            <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.ip') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.location') }}</th>
            <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.events') }}</th>
            <th class="px-3 py-2 font-medium text-right">{{ t('admin.usage.col.users') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.firstSeen') }}</th>
            <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.lastSeen') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ips" :key="row.ip" class="border-t border-gray-100 dark:border-gray-700">
            <td class="px-3 py-2 font-mono">{{ row.ip }}</td>
            <td class="px-3 py-2">{{ [row.city, row.country].filter(Boolean).join(', ') || '—' }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ row.events }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ row.unique_users }}</td>
            <td class="px-3 py-2 font-mono text-xs">{{ row.first_seen }}</td>
            <td class="px-3 py-2 font-mono text-xs">{{ row.last_seen }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Timeline --------------------------------------------------------- -->
    <div v-if="tab === 'timeline'">
      <!-- A real <form> so Enter in any input fires Apply via @submit. On
           Android, the soft keyboard's Go/Search button does the same thing.
           type="submit" on the Apply button keeps the click handler working
           the same way as before. -->
      <form @submit.prevent="loadTimeline(1)" class="grid md:grid-cols-5 gap-2 mb-3">
        <input v-model="timelineFilters.kind" :placeholder="t('admin.usage.timeline.kindPh')" class="text-sm px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800"/>
        <input v-model="timelineFilters.user" :placeholder="t('admin.usage.timeline.userPh')" class="text-sm px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800"/>
        <input v-model="timelineFilters.ip" :placeholder="t('admin.usage.timeline.ipPh')" class="text-sm px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800"/>
        <input v-model="timelineFilters.country" :placeholder="t('admin.usage.timeline.countryPh')" class="text-sm px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800"/>
        <button type="submit" class="text-sm px-3 py-1 bg-blue-600 text-white rounded">{{ t('admin.usage.timeline.apply') }}</button>
      </form>

      <div class="border border-gray-200 dark:border-gray-700 rounded overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="bg-gray-50 dark:bg-gray-800 text-left">
            <tr>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.ts') }}</th>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.user') }}</th>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.ip') }}</th>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.location') }}</th>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.kind') }}</th>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.path') }}</th>
              <th class="px-3 py-2 font-medium">{{ t('admin.usage.col.extra') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ev in timeline" :key="ev.id" class="border-t border-gray-100 dark:border-gray-700">
              <td class="px-3 py-2 font-mono text-xs whitespace-nowrap">{{ ev.ts }}</td>
              <td class="px-3 py-2 text-xs">
                <template v-if="ev.user_email">
                  <span class="text-gray-400 dark:text-gray-500 font-mono mr-1">{{ ev.user_id }} ·</span>{{ ev.user_email }}
                </template>
                <template v-else>(guest)</template>
              </td>
              <td class="px-3 py-2 font-mono text-xs">{{ ev.ip }}</td>
              <td class="px-3 py-2 text-xs">{{ [ev.geo_city, ev.geo_country].filter(Boolean).join(', ') || '—' }}</td>
              <td class="px-3 py-2 text-xs">{{ kindLabel(ev.kind) }}</td>
              <td class="px-3 py-2 max-w-xs truncate font-mono text-xs" :title="ev.path || ''">{{ ev.path }}</td>
              <td class="px-3 py-2 max-w-xs truncate font-mono text-xs text-gray-500" :title="formatExtra(ev.extra)">{{ formatExtra(ev.extra) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="timelineTotalPages > 1" class="flex items-center justify-center gap-2 mt-4 text-sm">
        <button
          type="button"
          :disabled="timelinePage <= 1"
          class="px-3 py-1 border border-gray-300 dark:border-gray-600 rounded disabled:opacity-50"
          @click="loadTimeline(timelinePage - 1)"
        >
          ‹ {{ t('myActivity.prev') }}
        </button>
        <span class="text-gray-600 dark:text-gray-400">{{ t('myActivity.pageOf', { p: timelinePage, n: timelineTotalPages }) }}</span>
        <button
          type="button"
          :disabled="timelinePage >= timelineTotalPages"
          class="px-3 py-1 border border-gray-300 dark:border-gray-600 rounded disabled:opacity-50"
          @click="loadTimeline(timelinePage + 1)"
        >
          {{ t('myActivity.next') }} ›
        </button>
      </div>
    </div>

    <!-- Settings -------------------------------------------------------- -->
    <div v-if="tab === 'settings' && settings && !loading" class="max-w-2xl border border-gray-200 dark:border-gray-700 rounded p-4">
      <h3 class="text-sm font-medium mb-1">{{ t('admin.usage.settings.title') }}</h3>
      <p class="text-xs text-gray-600 dark:text-gray-400 mb-4">{{ t('admin.usage.settings.subtitle') }}</p>

      <div class="space-y-2">
        <label
          v-for="kind in settings.all_kinds"
          :key="kind"
          class="flex items-center gap-3 cursor-pointer"
        >
          <input
            type="checkbox"
            v-model="settingsLocalEnabled[kind]"
            class="h-4 w-4 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
          />
          <span class="text-sm font-medium">{{ kindLabel(kind) }}</span>
          <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('admin.usage.settings.kindHint.' + kind) }}</span>
        </label>
      </div>

      <div class="mt-5 flex items-center gap-3">
        <button
          type="button"
          :disabled="settingsSaving"
          class="px-4 py-1.5 bg-blue-600 text-white text-sm rounded disabled:opacity-50"
          @click="saveSettings"
        >
          {{ settingsSaving ? t('admin.usage.settings.saving') : t('admin.usage.settings.save') }}
        </button>
        <span v-if="settingsSavedAt" class="text-xs text-green-600 dark:text-green-400">{{ t('admin.usage.settings.savedAt') }}</span>
      </div>
    </div>
  </div>
</template>
