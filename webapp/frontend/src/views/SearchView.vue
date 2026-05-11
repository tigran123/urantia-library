<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import { DocumentIcon, FolderIcon, MagnifyingGlassIcon, BookmarkIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'

const route = useRoute()
const matches = ref<any[]>([])
const loading = ref(false)
const searched = ref(false)
const error = ref('')
const favoritePaths = ref<Set<string>>(new Set())

const loadFavorites = async () => {
  try {
    const res = await api.get('/favorites')
    const paths = res.data.items.map((f: any) => f.path)
    favoritePaths.value = new Set(paths)
  } catch (err) {
    console.error('Failed to load favorites', err)
  }
}

const toggleFavorite = async (path: string, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  try {
    const newPaths = new Set(favoritePaths.value)
    if (favoritePaths.value.has(path)) {
      await api.delete(`/favorites/${encodeURIComponent(path)}`)
      newPaths.delete(path)
    } else {
      await api.post('/favorites', { item_path: path })
      newPaths.add(path)
    }
    favoritePaths.value = newPaths
  } catch (err) {
    console.error('Failed to toggle favorite', err)
  }
}

const doSearch = async (q: string) => {
  if (!q) {
    matches.value = []
    searched.value = false
    return
  }

  loading.value = true
  error.value = ''
  searched.value = true

  try {
    const res = await api.get('/search', { params: { q } })
    matches.value = res.data.matches
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadFavorites()
  doSearch(route.query.q as string)
})

watch(() => route.query.q, (newQ) => {
  doSearch(newQ as string)
})

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
    <div class="bg-white p-6 rounded-lg shadow-sm border border-gray-100">
      <h1 class="text-2xl font-bold text-gray-900 mb-2 flex items-center gap-2">
        <MagnifyingGlassIcon class="h-6 w-6 text-blue-600" />
        Search Results
      </h1>
      <p class="text-gray-500">
        Results for <span class="font-semibold text-gray-900">"{{ route.query.q }}"</span>
      </p>
    </div>

    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 text-red-600 p-4 rounded-lg border border-red-200">
      {{ error }}
    </div>

    <div v-else-if="searched && matches.length === 0" class="text-center py-20 text-gray-500 bg-white rounded-lg border border-gray-100 shadow-sm">
      <MagnifyingGlassIcon class="mx-auto h-12 w-12 text-gray-300 mb-3" />
      <p class="text-lg">No matches found.</p>
    </div>

    <div v-else-if="matches.length > 0" class="bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden">
      <div class="px-4 py-3 bg-gray-50 border-b border-gray-100 text-sm text-gray-500 font-medium">
        Found {{ matches.length }} matches (limited to 100)
      </div>
      <ul class="divide-y divide-gray-100">
        <li v-for="match in matches" :key="match.path" class="hover:bg-gray-50 transition-colors p-4 group">
          <div class="relative flex gap-4">
            <!-- Icon/Cover -->
            <div class="flex-shrink-0">
               <div v-if="match.is_dir" class="h-12 w-12 flex items-center justify-center bg-blue-50 rounded-lg">
                 <FolderIcon class="h-8 w-8 text-blue-400" />
               </div>
               <div v-else class="h-16 w-12 flex items-center justify-center bg-gray-100 rounded shadow-sm overflow-hidden border border-gray-200">
                 <img v-if="match.cover_url" :src="getFullUrl(match.cover_url)" class="w-full h-full object-contain" />
                 <DocumentIcon v-else class="h-6 w-6 text-gray-400" />
               </div>
            </div>

            <!-- Details -->
            <div class="flex-1 min-w-0 pr-12">
              <div class="flex items-start justify-between">
                <div>
                  <router-link v-if="!match.is_dir" :to="`/item/${match.path}`" class="text-lg font-medium text-blue-600 hover:underline break-words">
                    {{ formatFilename(match.name, match.is_dir) }}
                  </router-link>
                  <template v-else>
                    <a v-if="match.path.startsWith('Websites/')" :href="getFullUrl(`/api/files/${match.path.split('/').map(encodeURIComponent).join('/')}/`)" target="_blank" class="text-lg font-medium text-blue-600 hover:underline break-words">
                      {{ formatFilename(match.name, match.is_dir) }}
                    </a>
                    <router-link v-else :to="`/browse/${match.path}`" class="text-lg font-medium text-blue-600 hover:underline break-words">
                      {{ formatFilename(match.name, match.is_dir) }}
                    </router-link>
                  </template>
                  <p v-if="match.description" class="text-sm text-gray-600 mt-1 line-clamp-3" v-html="match.description.replace(new RegExp(route.query.q as string, 'gi'), (m: string) => `<mark class='bg-yellow-200'>${m}</mark>`)"></p>
                </div>
              </div>

              <div class="mt-2 text-xs text-gray-400 flex items-center gap-1">
                 {{ $t('app.location') }}
                 <router-link :to="`/browse/${match.parent_dir}`" class="hover:text-blue-500 hover:underline">
                   /{{ match.parent_dir || 'Root' }}
                 </router-link>
              </div>
            </div>

            <!-- Bookmark Button -->
            <button @click.prevent="toggleFavorite(match.path, $event)" class="absolute right-0 top-0 p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-blue-500': favoritePaths.has(match.path), 'text-gray-400 hover:text-blue-500': !favoritePaths.has(match.path) }" :title="favoritePaths.has(match.path) ? $t('app.remove_favorite') : $t('app.add_favorite')">
              <BookmarkIconSolid v-if="favoritePaths.has(match.path)" class="h-5 w-5" />
              <BookmarkIcon v-else class="h-5 w-5" />
            </button>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>