<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick, provide } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { MagnifyingGlassIcon, BookOpenIcon, ArrowRightOnRectangleIcon, QuestionMarkCircleIcon, XMarkIcon, BookmarkIcon, Cog6ToothIcon, ShieldCheckIcon } from '@heroicons/vue/24/outline'
import api from './api'
import { userInitials } from './userDisplay'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from './components/LanguageSwitcher.vue'
import ThemeSwitcher from './components/ThemeSwitcher.vue'
import SettingsModal from './components/SettingsModal.vue'

const { t } = useI18n({ useScope: 'global' })

const searchQuery = ref('')
const showSearchTips = ref(false)
const searchInputDesktop = ref<HTMLInputElement | null>(null)
const searchInputMobile = ref<HTMLInputElement | null>(null)
const router = useRouter()
const route = useRoute()

const currentUser = ref<{ email: string, avatar_url?: string, real_name?: string | null, search_per_page?: number | null, is_admin?: boolean, clearance?: number } | null>(null)
provide('currentUser', currentUser)
const isProfileMenuOpen = ref(false)
const isSettingsModalOpen = ref(false)

const isAuthRoute = computed(() => {
  return route.name === 'login' || route.name === 'register'
})

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
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'x') return
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

onMounted(() => {
  fetchCurrentUser()
  window.addEventListener('keydown', onGlobalShortcut)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalShortcut)
})

const currentBrowsePath = computed(() => {
  if (route.name === 'browse' && route.params.path) {
    const p = route.params.path;
    return Array.isArray(p) ? p.join('/') : p;
  }
  return '';
})

const performSearch = () => {
  if (searchQuery.value.trim()) {
    router.push({ name: 'search', query: { q: searchQuery.value } })
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
    router.push({ name: 'login' })
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
    <header class="bg-white dark:bg-gray-800 shadow-sm dark:shadow-gray-900/50 sticky top-0 z-30 border-b border-transparent dark:border-gray-700" v-if="!isAuthRoute">
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
                class="block w-full pl-10 pr-10 py-2 border border-gray-300 dark:border-gray-600 rounded-md leading-5 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-500 dark:placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                :placeholder="t('app.search_placeholder')"
              />
              <div class="absolute inset-y-0 right-0 pr-3 flex items-center">
                <button type="button" @click="showSearchTips = true" class="text-gray-400 hover:text-blue-500 focus:outline-none" title="Search Tips">
                  <QuestionMarkCircleIcon class="h-5 w-5" />
                </button>
              </div>
              <div v-if="currentBrowsePath && !searchQuery.includes('path:')" class="absolute top-full left-0 mt-1 pl-1 text-xs text-gray-500 dark:text-gray-400">
                <button type="button" @click="searchQuery = `path:${currentBrowsePath} ` + searchQuery" class="hover:text-blue-600 dark:hover:text-blue-400 hover:underline cursor-pointer">
                  Search in "{{ currentBrowsePath }}"
                </button>
              </div>
            </form>
          </div>

          <div class="flex items-center gap-2 sm:gap-4">
            <router-link to="/bookshelf" class="flex items-center gap-1 text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white focus:outline-none ml-2 mr-2">
              <BookmarkIcon class="h-5 w-5" />
              <span>{{ t('app.bookshelf') }}</span>
            </router-link>

            <!-- Theme Switcher -->
            <ThemeSwitcher />
            <!-- Language Switcher -->
            <LanguageSwitcher />

            <!-- User Profile Menu Dropdown -->
            <div class="relative ml-2">
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
            class="block w-full pl-10 pr-10 py-2 border border-gray-300 dark:border-gray-600 rounded-md leading-5 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-500 dark:placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            :placeholder="t('app.search_placeholder')"
          />
          <div class="absolute inset-y-0 right-0 pr-3 flex items-center">
            <button type="button" @click="showSearchTips = true" class="text-gray-400 hover:text-blue-500 focus:outline-none" title="Search Tips">
              <QuestionMarkCircleIcon class="h-5 w-5" />
            </button>
          </div>
          <div v-if="currentBrowsePath && !searchQuery.includes('path:')" class="absolute top-full left-0 mt-1 pl-1 text-xs text-gray-500 dark:text-gray-400">
            <button type="button" @click="searchQuery = `path:${currentBrowsePath} ` + searchQuery" class="hover:text-blue-600 dark:hover:text-blue-400 hover:underline cursor-pointer">
              Search in "{{ currentBrowsePath }}"
            </button>
          </div>
        </form>
      </div>
    </header>

    <!-- Main Content -->
    <main class="flex-1 w-full px-4 sm:px-6 lg:px-8 py-8">
      <router-view />
    </main>

    <footer class="bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 py-6 text-center text-sm text-gray-500 dark:text-gray-400 mt-auto">
      {{ t('app.footer') }}
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
  </div>
</template>
