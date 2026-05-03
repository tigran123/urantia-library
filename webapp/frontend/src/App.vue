<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { MagnifyingGlassIcon, BookOpenIcon, ArrowRightOnRectangleIcon } from '@heroicons/vue/24/outline'
import api from './api'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from './components/LanguageSwitcher.vue'

const { t } = useI18n({ useScope: 'global' })

const searchQuery = ref('')
const router = useRouter()
const route = useRoute()

const isAuthRoute = computed(() => {
  return route.name === 'login' || route.name === 'register'
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
  <div class="min-h-screen flex flex-col relative">
    <!-- Auth Language Switcher -->
    <div v-if="isAuthRoute" class="absolute top-4 right-4 z-20">
      <LanguageSwitcher />
    </div>

    <!-- Header -->
    <header class="bg-white shadow-sm sticky top-0 z-10" v-if="!isAuthRoute">
      <div class="w-full px-4 sm:px-6 lg:px-8">
        <div class="flex justify-between h-16 items-center">
          <div class="flex items-center">
            <router-link to="/" class="flex items-center gap-2 text-xl font-bold text-gray-900">
              <BookOpenIcon class="h-8 w-8 text-blue-600" />
              {{ t('app.title') }}
            </router-link>
          </div>

          <div class="flex-1 px-8 hidden sm:block">
            <form @submit.prevent="performSearch" class="relative">
              <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <MagnifyingGlassIcon class="h-5 w-5 text-gray-400" />
              </div>
              <input
                v-model="searchQuery"
                type="search"
                class="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-md leading-5 bg-white placeholder-gray-500 focus:outline-none focus:placeholder-gray-400 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                :placeholder="t('app.search_placeholder')"
              />
            </form>
          </div>

          <div class="flex items-center gap-4">
            <!-- Language Switcher -->
            <LanguageSwitcher />

            <button @click="handleLogout" class="flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 focus:outline-none">
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
            <MagnifyingGlassIcon class="h-5 w-5 text-gray-400" />
          </div>
          <input
            v-model="searchQuery"
            type="search"
            class="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-md leading-5 bg-white placeholder-gray-500 focus:outline-none focus:placeholder-gray-400 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            :placeholder="t('app.search_placeholder')"
          />
        </form>
      </div>
    </header>

    <!-- Main Content -->
    <main class="flex-1 w-full px-4 sm:px-6 lg:px-8 py-8">
      <router-view />
    </main>

    <footer class="bg-white border-t py-6 text-center text-sm text-gray-500 mt-auto">
      {{ t('app.footer') }}
    </footer>
  </div>
</template>
