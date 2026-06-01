<script setup lang="ts">
// /playlists — the index of the signed-in user's playlists (Bookshelf first).
import { ref, watch, inject, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'
import { BookmarkIcon, PlusIcon } from '@heroicons/vue/24/outline'
import PlaylistCard from '../components/PlaylistCard.vue'
import PlaylistEditDialog from '../components/PlaylistEditDialog.vue'
import { listPlaylists, type PlaylistSummary } from '../api'

const { t } = useI18n({ useScope: 'global' })
const currentUser = inject<Ref<{ email?: string; is_admin?: boolean } | null>>('currentUser', ref(null))

const playlists = ref<PlaylistSummary[]>([])
const loading = ref(true)
const error = ref('')
const showCreate = ref(false)

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await listPlaylists()
    playlists.value = res.data.items
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

const onCreated = async () => {
  showCreate.value = false
  await load()
}

// currentUser is fetched asynchronously in App.vue, so on a cold load/direct
// navigation it's null at mount and populates later. Watch by *identity*
// (email), not by ref — App.vue's /api/me heartbeat replaces the ref with a
// fresh object on each beat, and watching the ref would re-fire load() (and
// the spinner) every time. Email changes only on login/logout/switch.
watch(() => currentUser.value?.email ?? null, (email) => {
  if (email) load()
  else loading.value = false
}, { immediate: true })
</script>

<template>
  <div class="space-y-6">
    <!-- Header card -->
    <div class="bg-white dark:bg-gray-800 p-6 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 flex items-center justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <BookmarkIconSolid class="h-6 w-6 text-blue-600 dark:text-blue-400" />
          {{ t('playlists.title') }}
        </h1>
        <p class="text-gray-500 dark:text-gray-400 mt-1">{{ t('playlists.subtitle') }}</p>
      </div>
      <button
        v-if="currentUser"
        @click="showCreate = true"
        class="inline-flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 whitespace-nowrap"
      >
        <PlusIcon class="h-5 w-5" /> {{ t('playlists.new') }}
      </button>
    </div>

    <!-- Guest -->
    <div v-if="!currentUser" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
      <BookmarkIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
      <p class="text-lg">{{ t('app.bookshelf_signin') }}</p>
      <router-link :to="{ name: 'login' }" class="inline-block mt-4 px-5 py-2 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700">
        {{ t('auth.signInBtn') }}
      </router-link>
    </div>

    <div v-else-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 p-4 rounded-lg border border-red-200 dark:border-red-800">
      {{ error }}
    </div>

    <div v-else-if="playlists.length === 0" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
      <BookmarkIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
      <p class="text-lg">{{ t('playlists.empty') }}</p>
    </div>

    <div v-else class="grid gap-5 grid-cols-[repeat(auto-fill,minmax(260px,1fr))]">
      <PlaylistCard v-for="p in playlists" :key="p.id" :playlist="p" />
    </div>

    <PlaylistEditDialog v-if="showCreate" :playlist="null" @close="showCreate = false" @saved="onCreated" />
  </div>
</template>
