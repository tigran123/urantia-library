<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { DocumentIcon, FolderIcon, BookmarkIcon, TrashIcon, ArrowLeftIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'

const router = useRouter()
const favorites = ref<any[]>([])
const loading = ref(true)
const error = ref('')

const loadFavorites = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get('/favorites')
    favorites.value = res.data.items || []
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadFavorites()
})

const removeFavorite = async (path: string) => {
  try {
    await api.delete(`/favorites/${encodeURIComponent(path)}`)
    favorites.value = favorites.value.filter(f => f.path !== path)
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

    <div v-else-if="favorites.length === 0" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700 shadow-sm">
      <BookmarkIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
      <p class="text-lg">{{ $t('app.bookshelf_empty') }}</p>
    </div>

    <div v-else class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
      <ul class="divide-y divide-gray-100 dark:divide-gray-700">
        <li v-for="match in favorites" :key="match.path" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors p-4">
          <div class="flex gap-4">
            <!-- Icon/Cover -->
            <div class="flex-shrink-0">
               <div v-if="match.is_dir" class="h-12 w-12 flex items-center justify-center bg-blue-50 dark:bg-gray-700/50 rounded-lg">
                 <FolderIcon class="h-8 w-8 text-blue-400 dark:text-blue-500" />
               </div>
               <div v-else class="h-16 w-12 flex items-center justify-center bg-gray-100 dark:bg-gray-900 rounded shadow-sm overflow-hidden border border-gray-200 dark:border-gray-700">
                 <img v-if="match.cover_url" :src="getFullUrl(match.cover_url)" class="w-full h-full object-contain" />
                 <DocumentIcon v-else class="h-6 w-6 text-gray-400 dark:text-gray-600" />
               </div>
            </div>

            <!-- Details -->
            <div class="flex-1 min-w-0">
              <div class="flex items-start justify-between">
                <div>
                  <router-link v-if="!match.is_dir" :to="`/item/${match.path}`" class="text-lg font-medium text-blue-600 dark:text-blue-400 hover:underline break-words">
                    {{ formatFilename(match.name, match.is_dir) }}
                  </router-link>
                  <template v-else>
                    <a v-if="match.path.startsWith('Websites/')" :href="getFullUrl(`/api/files/${match.path.split('/').map(encodeURIComponent).join('/')}/`)" target="_blank" class="text-lg font-medium text-blue-600 dark:text-blue-400 hover:underline break-words">
                      {{ formatFilename(match.name, match.is_dir) }}
                    </a>
                    <router-link v-else :to="`/browse/${match.path}`" class="text-lg font-medium text-blue-600 dark:text-blue-400 hover:underline break-words">
                      {{ formatFilename(match.name, match.is_dir) }}
                    </router-link>
                  </template>
                  <p v-if="match.description" class="text-sm text-gray-600 dark:text-gray-400 mt-1 line-clamp-3" v-html="match.description"></p>
                </div>
                <button @click="removeFavorite(match.path)" class="text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-colors ml-4 p-2 rounded-full hover:bg-red-50 dark:hover:bg-red-900/30" :title="$t('app.remove_favorite')">
                  <TrashIcon class="h-5 w-5" />
                </button>
              </div>

              <div class="mt-2 text-xs text-gray-400 flex items-center gap-1">
                 {{ $t('app.location') }}
                 <router-link :to="`/browse/${match.path.split('/').slice(0, -1).join('/')}`" class="hover:text-blue-500 hover:underline">
                   /{{ match.path.split('/').slice(0, -1).join('/') || 'Root' }}
                 </router-link>
              </div>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>