<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick, provide } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { MagnifyingGlassIcon, BookOpenIcon, ArrowRightOnRectangleIcon, QuestionMarkCircleIcon, XMarkIcon, BookmarkIcon, Cog6ToothIcon, ShieldCheckIcon, ChatBubbleLeftRightIcon, InboxIcon, ClockIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'
import api, { getLibraryStats, type LibraryStats } from './api'
import { userInitials } from './userDisplay'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from './components/LanguageSwitcher.vue'
import ThemeSwitcher from './components/ThemeSwitcher.vue'
import SettingsModal from './components/SettingsModal.vue'
import LegalReacceptanceModal from './components/LegalReacceptanceModal.vue'
import type { CurrentUser } from './api'

const { t, n: nFmt } = useI18n({ useScope: 'global' })

const searchQuery = ref('')
const showSearchTips = ref(false)
const searchInputDesktop = ref<HTMLInputElement | null>(null)
const searchInputMobile = ref<HTMLInputElement | null>(null)
const router = useRouter()
const route = useRoute()

const currentUser = ref<CurrentUser | null>(null)
provide('currentUser', currentUser)

// Re-acceptance modal fires when LEGAL_VERSION in webapp/backend/database.py
// has advanced past what the signed-in user last accepted. The /api/me payload
// carries the server-computed `legal_acceptance_current` flag; the comparison
// itself lives on the backend so a stale client can't disagree.
//
// Exception: the privacy/terms routes — the modal itself sends users there to
// review the very documents it's asking them to accept, so it must NOT mount
// over those views or the link is unusable (in either the original tab or a
// target="_blank" new tab, since the SPA boots App.vue fresh in both).
const needsLegalReacceptance = computed(() =>
  !!currentUser.value
  && currentUser.value.legal_acceptance_current === false
  && route.name !== 'privacy'
  && route.name !== 'terms'
)

const onLegalAccepted = (user: CurrentUser) => {
  // Server returned the refreshed UserResponse — patch local state in place so
  // the modal unmounts immediately without waiting for the next /me heartbeat.
  currentUser.value = user
}
const isProfileMenuOpen = ref(false)
const isSettingsModalOpen = ref(false)

const isAuthRoute = computed(() => {
  return route.name === 'login' || route.name === 'register'
})

// Public share view (/#/p/:token) renders recipient chrome: wordmark + "Sign in
// to save", no app nav/search. Driven by the route's meta.publicChrome flag.
const isPublicChrome = computed(() => route.meta?.publicChrome === true)

const fetchCurrentUser = async () => {
  if (isAuthRoute.value) return
  try {
    const response = await api.get('/me')
    currentUser.value = response.data
  } catch (e) {
    console.error('Failed to fetch user', e)
    currentUser.value = null
  }
}

const getFullUrl = (url: string | undefined) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}

watch(isAuthRoute, (newVal) => {
  isProfileMenuOpen.value = false
  if (!newVal) {
    fetchCurrentUser()
  } else {
    currentUser.value = null
  }
})

// Ctrl/Cmd+X clears the global search box and focuses it. We skip the
// admin-books page so that view's own handler (which targets its local search
// box) wins, and skip auth routes where the global search isn't mounted.
// If the user has text selected in an editable field we yield to the native
// cut behaviour.
const onGlobalShortcut = (e: KeyboardEvent) => {
  if (!(e.ctrlKey || e.metaKey)) return
  const key = e.key.toLowerCase()

  // Ctrl/Cmd+, opens Settings. Chrome does not bind this shortcut, so
  // preventDefault is enough. Gated on signed-in users (the Settings entry
  // in the profile menu is only shown when signed in) and skipped on auth
  // routes where the modal isn't mounted.
  if (key === ',') {
    if (isAuthRoute.value || !currentUser.value) return
    e.preventDefault()
    isSettingsModalOpen.value = true
    return
  }

  // Ctrl/Cmd+X clears the global search box and focuses it. We skip the
  // admin-books page so that view's own handler (which targets its local
  // search box) wins, and skip auth routes where the global search isn't
  // mounted. If the user has text selected in an editable field we yield to
  // the native cut behaviour.
  if (key !== 'x') return
  if (route.name === 'admin-books' || isAuthRoute.value) return
  const active = document.activeElement as HTMLElement | null
  const inEditable = !!active && (
    active.tagName === 'INPUT' ||
    active.tagName === 'TEXTAREA' ||
    active.isContentEditable
  )
  const hasSelection = (window.getSelection()?.toString() || '').length > 0
  if (inEditable && hasSelection) return
  e.preventDefault()
  searchQuery.value = ''
  nextTick(() => {
    // Focus whichever search box is currently visible (offsetParent is null
    // when the responsive class hides the element).
    const target = searchInputDesktop.value?.offsetParent
      ? searchInputDesktop.value
      : searchInputMobile.value
    target?.focus()
  })
}

const stats = ref<LibraryStats | null>(null)
let statsTimer: number | null = null
const STATS_POLL_MS = 120_000

// Session-liveness heartbeat. Auth is stateless JWT in a cookie, so a session
// terminated server-side (admin "Terminate", or the user being deactivated)
// does not push anything to the browser. Without this poll the SPA would keep
// showing a signed-in UI until the next API call happens to fail with 401 —
// which on a static viewer page could be never.
let meHeartbeatTimer: number | null = null
// Sits just under the backend ONLINE_WINDOW (5 min / 300s): one heartbeat per
// window keeps idle tabs counted in "online users" / "Last seen" without the
// old per-30s log noise. The 30s gap below the window is deliberate margin —
// "online" is a look-back (last_seen within the last 300s), so the effective
// server-side refresh period (this interval + setInterval jitter + network
// latency) must stay under 300s, or an idle tab flickers offline in the sliver
// before each beat. Matching 300s exactly would leave zero margin.
const ME_HEARTBEAT_MS = 270_000

const heartbeatCurrentUser = async () => {
  // Guests get 401 on /me as their normal answer; nothing to watch for.
  if (!currentUser.value || isAuthRoute.value) return
  try {
    const response = await api.get('/me')
    currentUser.value = response.data
  } catch (e: any) {
    if (e.response?.status === 401) {
      currentUser.value = null
      if (!isAuthRoute.value) router.push({ name: 'login' })
    }
  }
}

const startMeHeartbeat = () => {
  if (meHeartbeatTimer !== null) return
  meHeartbeatTimer = window.setInterval(heartbeatCurrentUser, ME_HEARTBEAT_MS)
}

const stopMeHeartbeat = () => {
  if (meHeartbeatTimer !== null) {
    clearInterval(meHeartbeatTimer)
    meHeartbeatTimer = null
  }
}

const fetchStats = async () => {
  try {
    const r = await getLibraryStats()
    stats.value = r.data
  } catch {
    // Silent: footer just stays empty on transient errors.
  }
}

// Lets admin panels (e.g. Sessions Refresh) drive a footer refresh so the
// online-users / in-sessions count can't visibly disagree with the table.
provide('refreshStats', fetchStats)

// Auth state changes (login, logout, session expiry) flip which fields the
// /api/library-stats endpoint returns — refetch so the footer updates without
// a manual page reload. Watch by *identity* (email), not the ref: the /me
// heartbeat below replaces currentUser.value with a fresh object every
// ME_HEARTBEAT_MS, and watching the ref would refetch stats on every heartbeat
// for nothing.
watch(() => currentUser.value?.email ?? null, () => { fetchStats() })

const startStatsPolling = () => {
  if (statsTimer !== null) return
  statsTimer = window.setInterval(fetchStats, STATS_POLL_MS)
}

const stopStatsPolling = () => {
  if (statsTimer !== null) {
    clearInterval(statsTimer)
    statsTimer = null
  }
}

const onVisibilityChange = () => {
  if (document.hidden) {
    stopStatsPolling()
    stopMeHeartbeat()
  } else {
    fetchStats()
    startStatsPolling()
    heartbeatCurrentUser()
    startMeHeartbeat()
  }
}

onMounted(() => {
  fetchCurrentUser()
  window.addEventListener('keydown', onGlobalShortcut)
  fetchStats()
  startStatsPolling()
  startMeHeartbeat()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalShortcut)
  stopStatsPolling()
  stopMeHeartbeat()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})

const currentBrowsePath = computed(() => {
  if (route.name === 'browse' && route.params.path) {
    const p = route.params.path;
    return Array.isArray(p) ? p.join('/') : p;
  }
  return '';
})

// Mirrors the backend tokenizer in `parse_search_query` (main.py): a `path:`
// clause is either path:"quoted", path:'quoted', or path:<non-whitespace>.
const PATH_CLAUSE_RE = /(?:^|\s)path:(?:"[^"]*"|'[^']*'|\S+)/gi

const extractPathValues = (q: string): string[] => {
  const out: string[] = []
  for (const m of q.matchAll(PATH_CLAUSE_RE)) {
    let v = m[0].trim().slice('path:'.length)
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1)
    }
    out.push(v)
  }
  return out
}

// True only when the search box already filters by the current browse dir
// (case-insensitive, matching how the backend lowercases the value).
const hasCurrentPathFilter = computed(() => {
  if (!currentBrowsePath.value) return false
  const want = currentBrowsePath.value.toLowerCase()
  return extractPathValues(searchQuery.value).some(v => v.toLowerCase() === want)
})

const quotePathValueIfNeeded = (s: string): string => {
  if (!/\s/.test(s) && !s.includes('"') && !s.includes("'")) return s
  if (!s.includes('"')) return `"${s}"`
  if (!s.includes("'")) return `'${s}'`
  return s
}

const applyCurrentPathFilter = () => {
  if (!currentBrowsePath.value) return
  const stripped = searchQuery.value.replace(PATH_CLAUSE_RE, ' ').replace(/\s+/g, ' ').trim()
  const value = quotePathValueIfNeeded(currentBrowsePath.value)
  searchQuery.value = stripped ? `path:${value} ${stripped}` : `path:${value} `
}

// Set by onSearchEnter when Enter is pressed on a physical keyboard, so
// performSearch can leave focus alone. Defaults to false so non-keyboard
// submits (tapping the magnifier) also dismiss any on-screen keyboard.
let lastEnterFromPhysicalKey = false

// Heuristic: Android/iOS soft keyboards leave `event.code` empty for Enter,
// while a real key (USB/Bluetooth) populates it with 'Enter' or 'NumpadEnter'.
const onSearchEnter = (e: KeyboardEvent) => {
  lastEnterFromPhysicalKey = !!e.code
}

const performSearch = () => {
  const fromPhysical = lastEnterFromPhysicalKey
  lastEnterFromPhysicalKey = false
  if (searchQuery.value.trim()) {
    router.push({ name: 'search', query: { q: searchQuery.value } })
    if (!fromPhysical) {
      // Blur to dismiss the on-screen keyboard on touch devices. A no-op on
      // desktop, since focus only matters when a soft keyboard is up.
      searchInputDesktop.value?.blur()
      searchInputMobile.value?.blur()
    }
  }
}

// Keep the global search box in sync with the active search query, so a search
// triggered elsewhere (e.g. clicking an author link) lands in the box ready to
// be refined manually.
watch(() => route.query.q, (q) => {
  if (typeof q === 'string') searchQuery.value = q
}, { immediate: true })

const handleLogout = async () => {
  isProfileMenuOpen.value = false
  try {
    await api.post('/logout')
  } catch (e) {
    console.error(e)
  } finally {
    currentUser.value = null
    router.push({ name: 'browse' })
  }
}
</script>

<template>
  <div class="min-h-screen flex flex-col relative dark:bg-gray-900">
    <!-- Auth Language & Theme Switcher -->
    <div v-if="isAuthRoute" class="absolute top-4 right-4 z-20 flex items-center gap-2">
      <ThemeSwitcher />
      <LanguageSwitcher />
    </div>

    <!-- Header -->
    <header class="bg-white dark:bg-gray-800 shadow-sm dark:shadow-gray-900/50 sticky top-0 z-30 border-b border-transparent dark:border-gray-700" v-if="!isAuthRoute && !isPublicChrome">
      <div class="w-full px-4 sm:px-6 lg:px-8">
        <div class="flex justify-between h-16 items-center">
          <div class="flex items-center">
            <router-link to="/" class="flex items-center text-gray-900 dark:text-white" :title="t('app.title')">
              <BookOpenIcon class="h-8 w-8 text-blue-600 dark:text-blue-400" />
            </router-link>
          </div>

          <div class="flex-1 px-8 hidden sm:block">
            <form @submit.prevent="performSearch" class="relative">
              <button type="submit" class="absolute inset-y-0 left-0 pl-3 flex items-center text-gray-400 dark:text-gray-500 hover:text-blue-500 focus:outline-none" title="Search">
                <MagnifyingGlassIcon class="h-5 w-5" />
              </button>
              <input
                ref="searchInputDesktop"
                v-model="searchQuery"
                type="search"
                @keydown.enter="onSearchEnter"
                class="block w-full pl-10 pr-10 py-2 border border-gray-300 dark:border-gray-600 rounded-md leading-5 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-500 dark:placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                :placeholder="t('app.search_placeholder')"
              />
              <div class="absolute inset-y-0 right-0 pr-3 flex items-center">
                <button type="button" @click="showSearchTips = true" class="text-gray-400 hover:text-blue-500 focus:outline-none" title="Search Tips">
                  <QuestionMarkCircleIcon class="h-5 w-5" />
                </button>
              </div>
              <div v-if="currentBrowsePath && !hasCurrentPathFilter" class="absolute top-full left-0 mt-1 pl-1 text-xs text-gray-500 dark:text-gray-400">
                <button type="button" @click="applyCurrentPathFilter()" class="hover:text-blue-600 dark:hover:text-blue-400 hover:underline cursor-pointer">
                  {{ t('app.search_in_path', { path: currentBrowsePath }) }}
                </button>
              </div>
            </form>
          </div>

          <div class="flex items-center gap-2 sm:gap-4">
            <router-link
              v-if="currentUser"
              to="/playlists"
              class="flex items-center gap-1 text-sm focus:outline-none ml-2 mr-2"
              :class="$route.path.startsWith('/playlists') ? 'text-blue-600 dark:text-blue-400' : 'text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white'"
            >
              <BookmarkIconSolid v-if="$route.path.startsWith('/playlists')" class="h-5 w-5" />
              <BookmarkIcon v-else class="h-5 w-5" />
              <span>{{ t('playlists.title') }}</span>
            </router-link>

            <!-- Theme Switcher -->
            <ThemeSwitcher />
            <!-- Language Switcher -->
            <LanguageSwitcher />

            <!-- Guest: Sign in link in place of the profile menu -->
            <router-link
              v-if="!currentUser"
              :to="{ name: 'login' }"
              class="flex items-center gap-1 ml-2 text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white focus:outline-none"
            >
              <ArrowRightOnRectangleIcon class="h-5 w-5" />
              <span>{{ t('auth.signInBtn') }}</span>
            </router-link>

            <!-- User Profile Menu Dropdown -->
            <div v-else class="relative ml-2">
              <button @click="isProfileMenuOpen = !isProfileMenuOpen" class="flex items-center text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white focus:outline-none">
                <img v-if="currentUser?.avatar_url" :src="getFullUrl(currentUser.avatar_url)" class="h-8 w-8 object-cover rounded-full border border-gray-200 dark:border-gray-700" alt="Avatar" />
                <span
                  v-else
                  class="h-8 w-8 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-semibold text-gray-600 dark:text-gray-200"
                >{{ userInitials(currentUser) }}</span>
              </button>

              <!-- Invisible Overlay to handle clicking outside -->
              <div v-if="isProfileMenuOpen" @click="isProfileMenuOpen = false" class="fixed inset-0 z-40"></div>

              <!-- Dropdown Content -->
              <div v-if="isProfileMenuOpen" class="absolute right-0 mt-2 w-64 bg-white dark:bg-gray-800 rounded-md shadow-lg py-1 z-50 ring-1 ring-black ring-opacity-5 dark:ring-white dark:ring-opacity-10">
                <div class="px-4 py-2 text-sm text-gray-700 dark:text-gray-300 break-words font-medium truncate" :title="currentUser?.email">
                  {{ currentUser?.email || 'Loading...' }}
                </div>
                <hr class="border-gray-200 dark:border-gray-700 my-1" />

                <button @click="isSettingsModalOpen = true; isProfileMenuOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 focus:outline-none">
                  <Cog6ToothIcon class="h-4 w-4" />
                  {{ t('app.settings') }}
                </button>

                <router-link
                  :to="{ name: 'feedback-compose' }"
                  @click="isProfileMenuOpen = false"
                  class="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 focus:outline-none"
                >
                  <ChatBubbleLeftRightIcon class="h-4 w-4" />
                  {{ t('feedback.send') }}
                </router-link>

                <router-link
                  :to="{ name: 'feedback-mine' }"
                  @click="isProfileMenuOpen = false"
                  class="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 focus:outline-none"
                >
                  <InboxIcon class="h-4 w-4" />
                  {{ t('feedback.my') }}
                </router-link>

                <router-link
                  :to="{ name: 'my-activity' }"
                  @click="isProfileMenuOpen = false"
                  class="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 focus:outline-none"
                >
                  <ClockIcon class="h-4 w-4" />
                  {{ t('myActivity.menuLink') }}
                </router-link>

                <router-link
                  v-if="currentUser?.is_admin"
                  to="/admin/users"
                  @click="isProfileMenuOpen = false"
                  class="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 focus:outline-none"
                >
                  <ShieldCheckIcon class="h-4 w-4" />
                  {{ t('admin.title') }}
                </router-link>

                <button @click="handleLogout" class="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 focus:outline-none">
                  <ArrowRightOnRectangleIcon class="h-4 w-4" />
                  {{ t('app.logout') }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <!-- Mobile search -->
      <div class="sm:hidden px-4 pb-3">
        <form @submit.prevent="performSearch" class="relative">
          <button type="submit" class="absolute inset-y-0 left-0 pl-3 flex items-center text-gray-400 dark:text-gray-500 hover:text-blue-500 focus:outline-none" title="Search">
            <MagnifyingGlassIcon class="h-5 w-5" />
          </button>
          <input
            ref="searchInputMobile"
            v-model="searchQuery"
            type="search"
            @keydown.enter="onSearchEnter"
            class="block w-full pl-10 pr-10 py-2 border border-gray-300 dark:border-gray-600 rounded-md leading-5 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-500 dark:placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            :placeholder="t('app.search_placeholder')"
          />
          <div class="absolute inset-y-0 right-0 pr-3 flex items-center">
            <button type="button" @click="showSearchTips = true" class="text-gray-400 hover:text-blue-500 focus:outline-none" title="Search Tips">
              <QuestionMarkCircleIcon class="h-5 w-5" />
            </button>
          </div>
          <div v-if="currentBrowsePath && !hasCurrentPathFilter" class="absolute top-full left-0 mt-1 pl-1 text-xs text-gray-500 dark:text-gray-400">
            <button type="button" @click="applyCurrentPathFilter()" class="hover:text-blue-600 dark:hover:text-blue-400 hover:underline cursor-pointer">
              {{ t('app.search_in_path', { path: currentBrowsePath }) }}
            </button>
          </div>
        </form>
      </div>
    </header>

    <!-- Recipient header for public shared playlists: wordmark + Sign in to save -->
    <header v-else-if="isPublicChrome" class="bg-white dark:bg-gray-800 shadow-sm dark:shadow-gray-900/50 sticky top-0 z-30 border-b border-transparent dark:border-gray-700">
      <div class="w-full px-4 sm:px-6 lg:px-8">
        <div class="flex justify-between h-16 items-center">
          <router-link to="/" class="flex items-center gap-2 text-gray-900 dark:text-white" :title="t('app.title')">
            <BookOpenIcon class="h-8 w-8 text-blue-600 dark:text-blue-400" />
            <span class="font-semibold text-lg">{{ t('app.title') }}</span>
          </router-link>
          <div class="flex items-center gap-2 sm:gap-4">
            <ThemeSwitcher />
            <LanguageSwitcher />
            <router-link
              v-if="!currentUser"
              :to="{ name: 'login', query: { next: $route.fullPath } }"
              class="inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700"
            >
              <ArrowRightOnRectangleIcon class="h-5 w-5" />
              <span>{{ t('playlists.sign_in_to_save') }}</span>
            </router-link>
            <router-link v-else to="/playlists" class="flex items-center" :title="t('playlists.title')">
              <img v-if="currentUser?.avatar_url" :src="getFullUrl(currentUser.avatar_url)" class="h-8 w-8 object-cover rounded-full border border-gray-200 dark:border-gray-700" alt="Avatar" />
              <span v-else class="h-8 w-8 rounded-full border border-gray-200 dark:border-gray-700 bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-semibold text-gray-600 dark:text-gray-200">{{ userInitials(currentUser) }}</span>
            </router-link>
          </div>
        </div>
      </div>
    </header>

    <!-- Main Content -->
    <main class="flex-1 w-full px-4 sm:px-6 lg:px-8 py-8">
      <router-view />
    </main>

    <footer v-if="!isAuthRoute && !isPublicChrome" class="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 py-6 text-center text-sm text-gray-500 dark:text-gray-400 mt-auto">
      <template v-if="stats">
        <span>{{ t('app.stats.books', { n: nFmt(stats.total_books) }, stats.total_books) }}</span>
        <template v-if="stats.total_audio > 0">
          <span class="mx-2">·</span>
          <span>{{ t('app.stats.audio', { n: nFmt(stats.total_audio) }, stats.total_audio) }}</span>
        </template>
        <template v-if="stats.total_video > 0">
          <span class="mx-2">·</span>
          <span>{{ t('app.stats.video', { n: nFmt(stats.total_video) }, stats.total_video) }}</span>
        </template>
        <span class="mx-2">·</span>
        <span>{{ t('app.stats.directories', { n: nFmt(stats.total_directories) }, stats.total_directories) }}</span>
        <span class="mx-2">·</span>
        <span>{{ t('app.stats.languages', { n: nFmt(stats.total_languages) }, stats.total_languages) }}</span>
        <template v-if="stats.total_users !== undefined">
          <span class="mx-2">·</span>
          <span>{{ t('app.stats.users', { n: nFmt(stats.total_users) }, stats.total_users) }} <span class="text-green-600 dark:text-green-400 font-medium">({{ t('app.stats.online', { n: nFmt(stats.online_users ?? 0) }) }}<template v-if="(stats.online_sessions ?? 0) > (stats.online_users ?? 0)">{{ ' ' + t('app.stats.inSessions', { m: nFmt(stats.online_sessions ?? 0) }, stats.online_sessions ?? 0) }}</template>)</span></span>
        </template>
        <template v-if="stats.books_added_7d > 0">
          <span class="mx-2">·</span>
          <span>{{ t('app.stats.addedThisWeek', { n: nFmt(stats.books_added_7d) }, stats.books_added_7d) }}</span>
        </template>
      </template>
      <span v-else>&nbsp;</span>
      <div class="mt-2 text-xs">
        <router-link :to="{ name: 'privacy' }" class="hover:underline">{{ t('app.legal.privacy') }}</router-link>
        <span class="mx-2">·</span>
        <router-link :to="{ name: 'terms' }" class="hover:underline">{{ t('app.legal.terms') }}</router-link>
      </div>
    </footer>

    <!-- Search Tips Modal -->
    <div v-if="showSearchTips" class="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-0">
      <div class="fixed inset-0 transition-opacity" style="background-color: rgba(0, 0, 0, 0.2);" @click="showSearchTips = false"></div>

      <div class="relative bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-md p-6 overflow-hidden z-10 text-left border border-gray-200 dark:border-gray-700">
        <div class="absolute top-4 right-4">
          <button @click="showSearchTips = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus:outline-none">
            <XMarkIcon class="h-6 w-6" />
          </button>
        </div>

        <h3 class="text-xl font-bold text-gray-900 dark:text-white mb-4">{{ t('search.tips_title') }}</h3>

        <ul class="list-disc pl-5 space-y-3 text-sm text-gray-700 dark:text-gray-300">
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">harnum music</code> {{ t('search.tip_words') }}</li>
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">"music theory"</code> {{ t('search.tip_phrase') }}</li>
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">theor*</code> {{ t('search.tip_wildcard') }}</li>
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">-grammar</code> {{ t('search.tip_exclude') }}</li>
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">author:harnum</code> {{ t('search.tip_field') }}</li>
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">path:Law/</code> {{ t('search.tip_path') }}</li>
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">ext:djvu</code> {{ t('search.tip_ext_or') }} <code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">ext:pdf</code> {{ t('search.tip_ext') }}</li>
           <li>{{ t('search.tip_combine') }} <code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">path:History/ ext:epub rome</code></li>
        </ul>

        <div class="mt-6 flex justify-end">
          <button @click="showSearchTips = false" class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 focus:outline-none">
            {{ t('search.got_it') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Settings Modal -->
    <SettingsModal
      :is-open="isSettingsModalOpen"
      :user="currentUser"
      @close="isSettingsModalOpen = false"
      @update-user="currentUser = $event"
    />

    <!-- Legal re-acceptance modal — fires when LEGAL_VERSION advances past
         what the signed-in user last accepted. Stays mounted until the user
         either accepts (call /api/legal/accept, local state flips) or signs
         out. Anonymous viewers don't see it; `currentUser` is null for them. -->
    <LegalReacceptanceModal
      v-if="needsLegalReacceptance"
      @accepted="onLegalAccepted"
      @signout="handleLogout"
    />
  </div>
</template>
