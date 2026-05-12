<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { MagnifyingGlassIcon, BookOpenIcon, ArrowRightOnRectangleIcon, QuestionMarkCircleIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import api from './api'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from './components/LanguageSwitcher.vue'
import ThemeSwitcher from './components/ThemeSwitcher.vue'

const { t } = useI18n({ useScope: 'global' })

const searchQuery = ref('')
const showSearchTips = ref(false)
const router = useRouter()
const route = useRoute()

const isAuthRoute = computed(() => {
  return route.name === 'login' || route.name === 'register'
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

const handleLogout = async () => {
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
    <header class="bg-white dark:bg-gray-800 shadow-sm dark:shadow-gray-900/50 sticky top-0 z-10 border-b border-transparent dark:border-gray-700" v-if="!isAuthRoute">
      <div class="w-full px-4 sm:px-6 lg:px-8">
        <div class="flex justify-between h-16 items-center">
          <div class="flex items-center">
            <router-link to="/" class="flex items-center gap-2 text-xl font-bold text-gray-900 dark:text-white">
              <BookOpenIcon class="h-8 w-8 text-blue-600 dark:text-blue-400" />
              {{ t('app.title') }}
            </router-link>
          </div>

          <div class="flex-1 px-8 hidden sm:block">
            <form @submit.prevent="performSearch" class="relative">
              <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <MagnifyingGlassIcon class="h-5 w-5 text-gray-400 dark:text-gray-500" />
              </div>
              <input
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

            <button @click="handleLogout" class="flex items-center gap-1 text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white focus:outline-none ml-2">
              <ArrowRightOnRectangleIcon class="h-5 w-5" />
              <span class="hidden sm:inline">{{ t('app.logout') }}</span>
            </button>
          </div>
        </div>
      </div>
      <!-- Mobile search -->
      <div class="sm:hidden px-4 pb-3">
        <form @submit.prevent="performSearch" class="relative">
          <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <MagnifyingGlassIcon class="h-5 w-5 text-gray-400 dark:text-gray-500" />
          </div>
          <input
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
        
        <h3 class="text-xl font-bold text-gray-900 dark:text-white mb-4">Search Tips & Filters</h3>
        
        <ul class="list-disc pl-5 space-y-3 text-sm text-gray-700 dark:text-gray-300">
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">path:Law/</code> to search within a specific directory.</li>
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">ext:djvu</code> or <code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">ext:pdf</code> to find specific file types.</li>
           <li><code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">type:dir</code> to find only directories.</li>
           <li>Combine them: <code class="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-gray-900 dark:text-gray-100 font-mono">path:History/ ext:epub rome</code></li>
        </ul>
        
        <div class="mt-6 flex justify-end">
          <button @click="showSearchTips = false" class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 focus:outline-none">
            Got it
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
