<script setup lang="ts">
import { ref, onMounted, watch, inject, type Ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api from '../api'

const { t } = useI18n({ useScope: 'global' })
const currentUser = inject<Ref<{ is_admin?: boolean } | null>>('currentUser', ref(null))

const editBookClearance = async (item: any, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!item.hash_id) return
  const current = item.clearance ?? 0
  const raw = window.prompt(t('admin.clearance_prompt', { title: item.title || item.name }), String(current))
  if (raw === null) return
  const next = Number(raw)
  if (!Number.isFinite(next) || next < 0 || !Number.isInteger(next)) {
    alert(t('admin.clearance_invalid'))
    return
  }
  try {
    await api.put(`/admin/books/${encodeURIComponent(item.hash_id)}/clearance`, { clearance: next })
    item.clearance = next
  } catch (err: any) {
    alert(err.response?.data?.detail || err.message)
  }
}
import { FolderIcon, DocumentIcon, HomeIcon, ChevronRightIcon, Squares2X2Icon, ListBulletIcon, BookmarkIcon, ArrowDownTrayIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'

const route = useRoute()
const items = ref<any[]>([])
const loading = ref(true)
const error = ref('')
const currentPath = ref('')
const savedViewMode = localStorage.getItem('viewMode')
const viewMode = ref<'grid' | 'list'>(savedViewMode === 'list' ? 'list' : 'grid')
const favoriteIds = ref<Set<string>>(new Set())
const dirFavorites = ref<Set<string>>(new Set())

const loadFavorites = async () => {
  try {
    const res = await api.get('/favorites')
    const ids = res.data.items.map((f: any) => f.hash_id)
    favoriteIds.value = new Set(ids)
  } catch (err) {
    console.error('Failed to load favorites', err)
  }
}

const loadDirFavorites = async () => {
  try {
    const res = await api.get('/dir-favorites')
    dirFavorites.value = new Set((res.data.items || []).map((f: any) => f.path))
  } catch (err) {
    console.error('Failed to load directory favorites', err)
  }
}

const toggleFavorite = async (item: any, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!item.hash_id) return

  try {
    const newIds = new Set(favoriteIds.value)
    if (favoriteIds.value.has(item.hash_id)) {
      await api.delete(`/favorites/${encodeURIComponent(item.hash_id)}`)
      newIds.delete(item.hash_id)
    } else {
      await api.post('/favorites', { hash_id: item.hash_id })
      newIds.add(item.hash_id)
    }
    favoriteIds.value = newIds
  } catch (err) {
    console.error('Failed to toggle favorite', err)
  }
}

const toggleDirFavorite = async (path: string, event?: Event) => {
  if (event) {
    event.preventDefault()
    event.stopPropagation()
  }
  if (!path) return
  try {
    const newSet = new Set(dirFavorites.value)
    if (dirFavorites.value.has(path)) {
      await api.delete('/dir-favorites', { params: { path } })
      newSet.delete(path)
    } else {
      await api.post('/dir-favorites', { path })
      newSet.add(path)
    }
    dirFavorites.value = newSet
  } catch (err) {
    console.error('Failed to toggle directory favorite', err)
  }
}

watch(viewMode, (newMode) => {
  localStorage.setItem('viewMode', newMode)
})

const formatBytes = (bytes: number, decimals = 2) => {
    if (!+bytes) return '0 Bytes'
    const k = 1024
    const dm = decimals < 0 ? 0 : decimals
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const loadPath = async (path: string) => {
  loading.value = true
  error.value = ''
  try {
    const p = Array.isArray(path) ? path.join('/') : path || ''
    currentPath.value = p
    const res = await api.get('/browse', { params: { path: p } })
    items.value = res.data.items
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadFavorites()
  loadDirFavorites()
  loadPath(route.params.path as string)
})

watch(() => route.params.path, (newPath) => {
  loadPath(newPath as string)
})

const getBreadcrumbs = () => {
  if (!currentPath.value) return []
  const parts = currentPath.value.split('/').filter(Boolean)
  return parts.map((part, index) => {
    return {
      name: part,
      path: '/' + parts.slice(0, index + 1).join('/')
    }
  })
}

const getFullUrl = (url: string) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}

const fileTypeLabel = (name: string): string | null => {
  if (!name) return null
  const n = name.toLowerCase()
  if (n.endsWith('.fb2.zip') || n.endsWith('.fb2')) return 'FB2'
  if (n.endsWith('.html.zip') || n.endsWith('.htm.zip')) return 'HTML'
  const m = n.match(/\.([^.]+)$/)
  if (!m) return null
  const labels: Record<string, string> = {
    pdf: 'PDF',
    djvu: 'DjVu',
    epub: 'ePub',
    txt: 'TXT',
    md: 'MD',
    markdown: 'MD',
    mobi: 'MOBI',
    azw: 'AZW',
    azw3: 'AZW3',
    html: 'HTML',
    htm: 'HTML',
  }
  return labels[m[1]] || null
}

const downloadItem = (item: any, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!item || item.is_dir) return
  const url = getFullUrl(`/api/files/${item.path.split('/').map(encodeURIComponent).join('/')}`)
  const a = document.createElement('a')
  a.href = url
  a.download = item.name
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
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
    <!-- Toolbar -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white dark:bg-gray-800 p-4 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700">

      <!-- Breadcrumbs -->
      <nav class="flex text-sm font-medium text-gray-500 dark:text-gray-400 overflow-x-auto" aria-label="Breadcrumb">
        <ol class="inline-flex items-center space-x-1 md:space-x-3 whitespace-nowrap">
          <li class="inline-flex items-center">
            <router-link to="/browse" class="inline-flex items-center hover:text-blue-600 dark:hover:text-blue-400 transition-colors">
              <HomeIcon class="h-4 w-4 mr-2" />
              Root
            </router-link>
          </li>
          <li v-for="crumb in getBreadcrumbs()" :key="crumb.path">
            <div class="flex items-center">
              <ChevronRightIcon class="h-4 w-4 text-gray-400 dark:text-gray-600 mx-1" />
              <router-link :to="`/browse${crumb.path}`" class="hover:text-blue-600 dark:hover:text-blue-400 transition-colors">
                {{ crumb.name }}
              </router-link>
            </div>
          </li>
        </ol>
      </nav>

      <div class="flex items-center gap-2 self-start sm:self-auto">
        <!-- Bookmark the current directory -->
        <button
          v-if="currentPath"
          @click="toggleDirFavorite(currentPath)"
          class="p-1.5 rounded-md transition-colors border"
          :class="dirFavorites.has(currentPath)
            ? 'text-blue-500 border-blue-200 dark:border-blue-700 bg-blue-50 dark:bg-blue-900/30 hover:bg-blue-100 dark:hover:bg-blue-900/50'
            : 'text-gray-500 dark:text-gray-400 border-transparent hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-gray-700'"
          :title="dirFavorites.has(currentPath) ? $t('app.remove_favorite') : $t('app.add_favorite')"
        >
          <BookmarkIconSolid v-if="dirFavorites.has(currentPath)" class="h-5 w-5" />
          <BookmarkIcon v-else class="h-5 w-5" />
        </button>

        <!-- View Toggle -->
        <div class="flex bg-gray-100 dark:bg-gray-900 rounded-lg p-1 border border-transparent dark:border-gray-700">
          <button
            @click="viewMode = 'grid'"
            :class="['p-1.5 rounded-md transition-colors', viewMode === 'grid' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']"
            :title="$t('app.grid_view')"
          >
            <Squares2X2Icon class="h-5 w-5" />
          </button>
          <button
            @click="viewMode = 'list'"
            :class="['p-1.5 rounded-md transition-colors', viewMode === 'list' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']"
            :title="$t('app.list_view')"
          >
            <ListBulletIcon class="h-5 w-5" />
          </button>
        </div>
      </div>
    </div>

    <!-- Content -->
    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 p-4 rounded-lg border border-red-200 dark:border-red-800">
      {{ error }}
    </div>

    <div v-else-if="items.length === 0" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700 shadow-sm">
      <FolderIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
      <p class="text-lg">{{ $t('app.empty_directory') }}</p>
    </div>

    <template v-else>
      <!-- Grid View -->
      <div v-if="viewMode === 'grid'" class="grid gap-6 grid-cols-[repeat(auto-fill,minmax(180px,1fr))]">
        <template v-for="item in items" :key="item.name">
          <div class="relative group">
            <button v-if="item.hash_id" @click.prevent="toggleFavorite(item, $event)" class="absolute top-2 right-2 z-10 p-1.5 rounded-full bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-700 shadow-sm backdrop-blur-sm transition-colors border border-gray-100 dark:border-gray-600" :class="{ 'text-blue-500': favoriteIds.has(item.hash_id), 'text-gray-400 hover:text-blue-500': !favoriteIds.has(item.hash_id) }" :title="favoriteIds.has(item.hash_id) ? $t('app.remove_favorite') : $t('app.add_favorite')">
              <BookmarkIconSolid v-if="favoriteIds.has(item.hash_id)" class="h-5 w-5" />
              <BookmarkIcon v-else class="h-5 w-5" />
            </button>
            <button
              v-if="currentUser?.is_admin && item.hash_id"
              @click.prevent="editBookClearance(item, $event)"
              class="absolute top-2 left-2 z-10 px-1.5 py-0.5 rounded text-xs font-mono bg-amber-100 dark:bg-amber-900/70 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-700 hover:bg-amber-200 dark:hover:bg-amber-800"
              :title="t('admin.edit_clearance_tooltip')"
            >🔒 {{ item.clearance ?? 0 }}</button>
            <button v-else-if="item.is_dir" @click.prevent="toggleDirFavorite(item.path, $event)" class="absolute top-2 right-2 z-10 p-1.5 rounded-full bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-700 shadow-sm backdrop-blur-sm transition-colors border border-gray-100 dark:border-gray-600" :class="{ 'text-blue-500': dirFavorites.has(item.path), 'text-gray-400 hover:text-blue-500': !dirFavorites.has(item.path) }" :title="dirFavorites.has(item.path) ? $t('app.remove_favorite') : $t('app.add_favorite')">
              <BookmarkIconSolid v-if="dirFavorites.has(item.path)" class="h-5 w-5" />
              <BookmarkIcon v-else class="h-5 w-5" />
            </button>
            <template v-if="item.is_dir">
              <a v-if="currentPath.startsWith('Websites')" :href="getFullUrl(`/api/files/${item.path.split('/').map(encodeURIComponent).join('/')}/`)" target="_blank" class="flex flex-col items-center p-4 bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 hover:shadow-md transition-all hover:border-blue-300 dark:hover:border-blue-500">
                <div class="aspect-square flex items-center justify-center w-full bg-blue-50/50 dark:bg-gray-700/50 rounded-lg mb-3 group-hover:bg-blue-50 dark:group-hover:bg-gray-700 transition-colors">
                  <FolderIcon class="h-16 w-16 text-blue-400 dark:text-blue-500 group-hover:text-blue-500 dark:group-hover:text-blue-400" />
                </div>
                <h3 class="text-sm font-medium text-gray-900 dark:text-gray-100 text-center w-full break-words" :title="item.name">{{ formatFilename(item.name, item.is_dir) }}</h3>
                <p v-if="item.description" class="text-xs text-gray-500 dark:text-gray-400 mt-1 text-center line-clamp-3" :title="item.description" v-html="item.description"></p>
              </a>
              <router-link v-else :to="`/browse/${currentPath ? currentPath + '/' : ''}${item.name}`" class="flex flex-col items-center p-4 bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 hover:shadow-md transition-all hover:border-blue-300 dark:hover:border-blue-500">
                <div class="aspect-square flex items-center justify-center w-full bg-blue-50/50 dark:bg-gray-700/50 rounded-lg mb-3 group-hover:bg-blue-50 dark:group-hover:bg-gray-700 transition-colors">
                  <FolderIcon class="h-16 w-16 text-blue-400 dark:text-blue-500 group-hover:text-blue-500 dark:group-hover:text-blue-400" />
                </div>
                <h3 class="text-sm font-medium text-gray-900 dark:text-gray-100 text-center w-full break-words" :title="item.name">{{ formatFilename(item.name, item.is_dir) }}</h3>
                <p v-if="item.description" class="text-xs text-gray-500 dark:text-gray-400 mt-1 text-center line-clamp-3" :title="item.description" v-html="item.description"></p>
              </router-link>
            </template>

            <router-link v-else :to="`/item/${currentPath ? currentPath + '/' : ''}${item.name}`" class="flex flex-col items-center p-4 bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 hover:shadow-md transition-all hover:border-green-300 dark:hover:border-green-500">
              <div class="aspect-[3/4] w-full mb-3 rounded-lg overflow-hidden flex items-center justify-center bg-gray-50 dark:bg-gray-900 relative">
                <img v-if="item.cover_url" :src="getFullUrl(item.cover_url)" :alt="item.name" class="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300" />
                <DocumentIcon v-else class="h-16 w-16 text-gray-300 dark:text-gray-600" />
                <button
                  @click.prevent.stop="downloadItem(item, $event)"
                  class="absolute bottom-2 left-2 z-10 p-1.5 rounded-full bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-700 shadow-sm backdrop-blur-sm border border-gray-100 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:text-blue-500"
                  :title="$t('app.download')"
                >
                  <ArrowDownTrayIcon class="h-5 w-5" />
                </button>
                <span
                  v-if="fileTypeLabel(item.name)"
                  class="absolute bottom-2 right-2 z-10 px-1.5 py-0.5 rounded text-xs font-mono font-semibold bg-gray-800/80 text-white backdrop-blur-sm"
                >{{ fileTypeLabel(item.name) }}</span>
              </div>
              <h3 class="text-sm font-medium text-gray-900 dark:text-gray-100 text-center w-full break-words line-clamp-2" :title="item.title || item.name">{{ item.title || formatFilename(item.name, item.is_dir) }}</h3>
              <p v-if="item.author" class="text-xs text-gray-500 dark:text-gray-400 mt-1 text-center w-full truncate font-bold italic" :title="item.author">{{ item.author }}</p>
              <p v-if="item.description" class="text-xs text-gray-500 dark:text-gray-400 mt-1 text-center line-clamp-3" :title="item.description" v-html="item.description"></p>
            </router-link>
          </div>
        </template>
      </div>

      <!-- List View -->
      <div v-else class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
        <ul class="divide-y divide-gray-100 dark:divide-gray-700">
          <li v-for="item in items" :key="item.name" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors group">
            <div class="flex items-center p-4 gap-3">
              <template v-if="item.is_dir">
                <a v-if="currentPath.startsWith('Websites')" :href="getFullUrl(`/api/files/${item.path.split('/').map(encodeURIComponent).join('/')}/`)" target="_blank" class="flex-1 flex items-center min-w-0">
                  <FolderIcon class="h-8 w-8 text-blue-400 dark:text-blue-500 flex-shrink-0 mr-4" />
                  <div class="flex-1 min-w-0">
                    <p class="text-sm font-medium text-gray-900 dark:text-gray-100 break-words">{{ formatFilename(item.name, item.is_dir) }}</p>
                    <p v-if="item.description" class="text-xs text-gray-500 dark:text-gray-400 line-clamp-3 mt-0.5" v-html="item.description"></p>
                  </div>
                </a>
                <router-link v-else :to="`/browse/${currentPath ? currentPath + '/' : ''}${item.name}`" class="flex-1 flex items-center min-w-0">
                  <FolderIcon class="h-8 w-8 text-blue-400 dark:text-blue-500 flex-shrink-0 mr-4" />
                  <div class="flex-1 min-w-0">
                    <p class="text-sm font-medium text-gray-900 dark:text-gray-100 break-words">{{ formatFilename(item.name, item.is_dir) }}</p>
                    <p v-if="item.description" class="text-xs text-gray-500 dark:text-gray-400 line-clamp-3 mt-0.5" v-html="item.description"></p>
                  </div>
                </router-link>
              </template>

              <router-link v-else :to="`/item/${currentPath ? currentPath + '/' : ''}${item.name}`" class="flex-1 flex items-center min-w-0">
                <div class="h-12 w-10 flex-shrink-0 mr-4 rounded bg-gray-100 dark:bg-gray-900 flex items-center justify-center overflow-hidden">
                  <img v-if="item.cover_url" :src="getFullUrl(item.cover_url)" class="w-full h-full object-contain" />
                  <DocumentIcon v-else class="h-6 w-6 text-gray-400 dark:text-gray-600" />
                </div>
                <div class="flex-1 min-w-0">
                  <p class="text-sm font-medium text-gray-900 dark:text-gray-100 break-words line-clamp-2" :title="item.title || item.name">{{ item.title || formatFilename(item.name, item.is_dir) }}</p>
                  <p v-if="item.author" class="text-xs text-gray-600 dark:text-gray-300 truncate mt-0.5 font-bold italic" :title="item.author">{{ item.author }}</p>
                  <p v-if="item.description" class="text-xs text-gray-500 dark:text-gray-400 line-clamp-3 mt-0.5" v-html="item.description"></p>
                </div>
              </router-link>

              <div class="flex-shrink-0 flex flex-col items-end gap-1">
                <div class="flex items-center gap-1">
                  <button
                    v-if="currentUser?.is_admin && item.hash_id"
                    @click.prevent="editBookClearance(item, $event)"
                    class="px-1.5 py-0.5 rounded text-xs font-mono bg-amber-100 dark:bg-amber-900/70 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-700 hover:bg-amber-200 dark:hover:bg-amber-800"
                    :title="t('admin.edit_clearance_tooltip')"
                  >🔒 {{ item.clearance ?? 0 }}</button>
                  <button v-if="item.hash_id" @click.prevent="toggleFavorite(item, $event)" class="p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors" :class="{ 'text-blue-500': favoriteIds.has(item.hash_id), 'text-gray-400 hover:text-blue-500': !favoriteIds.has(item.hash_id) }" :title="favoriteIds.has(item.hash_id) ? $t('app.remove_favorite') : $t('app.add_favorite')">
                    <BookmarkIconSolid v-if="favoriteIds.has(item.hash_id)" class="h-5 w-5" />
                    <BookmarkIcon v-else class="h-5 w-5" />
                  </button>
                  <button v-else-if="item.is_dir" @click.prevent="toggleDirFavorite(item.path, $event)" class="p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors" :class="{ 'text-blue-500': dirFavorites.has(item.path), 'text-gray-400 hover:text-blue-500': !dirFavorites.has(item.path) }" :title="dirFavorites.has(item.path) ? $t('app.remove_favorite') : $t('app.add_favorite')">
                    <BookmarkIconSolid v-if="dirFavorites.has(item.path)" class="h-5 w-5" />
                    <BookmarkIcon v-else class="h-5 w-5" />
                  </button>
                </div>
                <div v-if="!item.is_dir" class="text-sm text-gray-500 dark:text-gray-400 whitespace-nowrap">
                  <span v-if="fileTypeLabel(item.name)" class="font-semibold">{{ fileTypeLabel(item.name) }}</span><span v-if="fileTypeLabel(item.name)"> · </span>{{ formatBytes(item.size) }}
                </div>
              </div>
            </div>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>
