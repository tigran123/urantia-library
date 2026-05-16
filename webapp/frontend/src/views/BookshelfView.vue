<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { DocumentIcon, BookmarkIcon, TrashIcon, ArrowLeftIcon, FolderIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'

const router = useRouter()
const favorites = ref<any[]>([])
const dirFavorites = ref<any[]>([])
const loading = ref(true)
const error = ref('')

const loadAll = async () => {
  loading.value = true
  error.value = ''
  try {
    const [favRes, dirRes] = await Promise.all([
      api.get('/favorites'),
      api.get('/dir-favorites'),
    ])
    favorites.value = favRes.data.items || []
    dirFavorites.value = dirRes.data.items || []
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadAll()
})

const removeFavorite = async (hash_id: string) => {
  try {
    await api.delete(`/favorites/${encodeURIComponent(hash_id)}`)
    favorites.value = favorites.value.filter(f => f.hash_id !== hash_id)
  } catch (err: any) {
    console.error(err)
  }
}

const removeDirFavorite = async (path: string) => {
  try {
    await api.delete('/dir-favorites', { params: { path } })
    dirFavorites.value = dirFavorites.value.filter(f => f.path !== path)
  } catch (err: any) {
    console.error(err)
  }
}

const getFullUrl = (url: string) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}

const formatFilename = (name: string, isDir: boolean, maxLength: number = 32) => {
  if (isDir || name.length <= maxLength) return name;
  const extIndex = name.lastIndexOf('.');
  if (extIndex === -1 || extIndex === 0) return name;

  const ext = name.substring(extIndex);
  const baseName = name.substring(0, extIndex);
  const keepLength = maxLength - ext.length - 3;

  if (keepLength <= 0) return name;
  return `${baseName.substring(0, keepLength)}...${ext}`;
}
</script>

<template>
  <div class="space-y-6">
    <div class="bg-white dark:bg-gray-800 p-6 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700">
      <div class="flex items-center gap-4 mb-2">
        <button @click="router.back()" class="p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-gray-500 dark:text-gray-400">
          <ArrowLeftIcon class="h-6 w-6" />
        </button>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-white flex items-center gap-2">
          <BookmarkIconSolid class="h-6 w-6 text-blue-600 dark:text-blue-400" />
          {{ $t('app.bookshelf') }}
        </h1>
      </div>
      <p class="text-gray-500 dark:text-gray-400 pl-14">
        {{ $t('app.bookshelf_desc') }}
      </p>
    </div>

    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 p-4 rounded-lg border border-red-200 dark:border-red-800">
      {{ error }}
    </div>

    <div v-else-if="favorites.length === 0 && dirFavorites.length === 0" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700 shadow-sm">
      <BookmarkIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
      <p class="text-lg">{{ $t('app.bookshelf_empty') }}</p>
    </div>

    <div v-else class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
      <ul class="divide-y divide-gray-100 dark:divide-gray-700">
        <!-- Directory bookmarks first -->
        <li v-for="dir in dirFavorites" :key="`dir:${dir.path}`" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors group">
          <div class="relative flex items-center">
            <router-link :to="`/browse/${dir.path}`" :class="['flex-1 flex items-center p-4 pr-16 min-w-0', !dir.exists && 'cursor-not-allowed']">
              <div class="h-12 w-10 flex-shrink-0 mr-4 rounded bg-blue-50/50 dark:bg-gray-900 flex items-center justify-center" :class="{ 'opacity-60': !dir.exists }">
                <FolderIcon class="h-6 w-6 text-blue-400 dark:text-blue-500" />
              </div>
              <div class="flex-1 min-w-0 pr-8" :class="{ 'opacity-60': !dir.exists }">
                <p class="text-sm font-medium text-gray-900 dark:text-gray-100 break-words line-clamp-2" :title="dir.path">{{ dir.name }}</p>
                <p class="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5" :title="dir.path">/{{ dir.path }}</p>
                <span v-if="!dir.exists" class="inline-block mt-1 px-2 py-0.5 text-xs font-medium rounded bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">{{ $t('app.missing_file') }}</span>
              </div>
            </router-link>
            <button @click="removeDirFavorite(dir.path)" class="absolute right-4 p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors text-gray-400 hover:text-red-500 dark:hover:text-red-400" :title="$t('app.remove_favorite')">
              <TrashIcon class="h-5 w-5" />
            </button>
          </div>
        </li>

        <li v-for="match in favorites" :key="match.hash_id" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors group">
          <div class="relative flex items-center">
            <component
              :is="match.path ? 'router-link' : 'div'"
              v-bind="match.path ? { to: `/item/${match.path}` } : {}"
              :class="['flex-1 flex items-center p-4 pr-16 min-w-0', !match.path && 'cursor-not-allowed']"
            >
              <div class="h-12 w-10 flex-shrink-0 mr-4 rounded bg-gray-100 dark:bg-gray-900 flex items-center justify-center overflow-hidden relative" :class="{ 'opacity-60': !match.path }">
                <DocumentIcon class="absolute h-6 w-6 text-gray-400 dark:text-gray-600 z-0" />
                <img :src="getFullUrl(`/api/covers/${match.hash_id}`)" @error="($event.target as HTMLImageElement).style.display = 'none'" class="relative z-10 w-full h-full object-contain bg-gray-100 dark:bg-gray-900" />
              </div>
              <div class="flex-1 min-w-0 pr-8" :class="{ 'opacity-60': !match.path }">
                <p class="text-sm font-medium text-gray-900 dark:text-gray-100 break-words line-clamp-2" :title="match.title || match.original_filename">{{ match.title || formatFilename(match.original_filename || '', false) }}</p>
                <p v-if="match.author" class="text-xs text-gray-600 dark:text-gray-300 truncate mt-0.5" :title="match.author">{{ match.author }}</p>
                <span v-if="!match.path" class="inline-block mt-1 px-2 py-0.5 text-xs font-medium rounded bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">{{ $t('app.missing_file') }}</span>
                <p v-if="match.description" class="text-xs text-gray-500 dark:text-gray-400 line-clamp-3 mt-0.5" v-html="match.description"></p>
              </div>
            </component>
            <button @click="removeFavorite(match.hash_id)" class="absolute right-4 p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors text-gray-400 hover:text-red-500 dark:hover:text-red-400" :title="$t('app.remove_favorite')">
              <TrashIcon class="h-5 w-5" />
            </button>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>