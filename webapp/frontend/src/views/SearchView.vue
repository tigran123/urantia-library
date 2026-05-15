<script setup lang="ts">
import { ref, onMounted, watch, computed, inject, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import { DocumentIcon, MagnifyingGlassIcon, BookmarkIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid, XMarkIcon } from '@heroicons/vue/24/solid'

const route = useRoute()
const router = useRouter()
const matches = ref<any[]>([])
const loading = ref(false)
const searched = ref(false)
const error = ref('')
const favoriteIds = ref<Set<string>>(new Set())

const DEFAULT_PER_PAGE = 50
const currentUser = inject<Ref<{ search_per_page?: number | null } | null>>(
  'currentUser',
  ref(null)
)
const perPage = computed(() => currentUser.value?.search_per_page ?? DEFAULT_PER_PAGE)
const total = ref(0)
const totalPages = ref(0)
const currentPage = computed(() => {
  const p = parseInt((route.query.page as string) || '1', 10)
  return isNaN(p) || p < 1 ? 1 : p
})

const goToPage = (page: number) => {
  router.push({ name: 'search', query: { ...route.query, page: String(page) } })
}

const parsedSearch = computed(() => {
  const q = (route.query.q as string) || ''
  let text = q
  const filters: {key: string, value: string, fullMatch: string}[] = []

  const pathMatch = text.match(/path:([^\s]+)/)
  if (pathMatch) {
    filters.push({ key: 'Path', value: pathMatch[1].replace(/['"]/g, ''), fullMatch: pathMatch[0] })
    text = text.replace(pathMatch[0], '')
  }

  const extMatch = text.match(/ext:([^\s]+)/)
  if (extMatch) {
    filters.push({ key: 'Extension', value: extMatch[1].replace(/['"]/g, ''), fullMatch: extMatch[0] })
    text = text.replace(extMatch[0], '')
  }

  return {
    text: text.trim(),
    filters
  }
})

const removeFilter = (fullMatch: string) => {
  const currentQ = route.query.q as string || ''
  const newQ = currentQ.replace(fullMatch, '').replace(/\s+/g, ' ').trim()
  router.push({ name: 'search', query: { q: newQ } })
}

const loadFavorites = async () => {
  try {
    const res = await api.get('/favorites')
    const ids = res.data.items.map((f: any) => f.hash_id)
    favoriteIds.value = new Set(ids)
  } catch (err) {
    console.error('Failed to load favorites', err)
  }
}

const toggleFavorite = async (item: any, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!item.hash_id) return
  const id = item.hash_id
  try {
    const newIds = new Set(favoriteIds.value)
    if (favoriteIds.value.has(id)) {
      await api.delete(`/favorites/${encodeURIComponent(id)}`)
      newIds.delete(id)
    } else {
      await api.post('/favorites', { hash_id: id })
      newIds.add(id)
    }
    favoriteIds.value = newIds
  } catch (err) {
    console.error('Failed to toggle favorite', err)
  }
}

const doSearch = async (q: string, page: number) => {
  if (!q) {
    matches.value = []
    total.value = 0
    totalPages.value = 0
    searched.value = false
    return
  }

  loading.value = true
  error.value = ''
  searched.value = true

  try {
    const res = await api.get('/search', { params: { q, page, per_page: perPage.value } })
    matches.value = res.data.matches
    total.value = res.data.total ?? 0
    totalPages.value = res.data.total_pages ?? 0
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadFavorites()
  doSearch(route.query.q as string, currentPage.value)
})

watch(() => [route.query.q, route.query.page], () => {
  doSearch(route.query.q as string, currentPage.value)
})

watch(perPage, () => {
  if (!searched.value) return
  if (currentPage.value !== 1) {
    router.replace({ name: 'search', query: { ...route.query, page: '1' } })
  } else {
    doSearch(route.query.q as string, 1)
  }
})

const getFullUrl = (url: string) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}

const escapeRegex = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;')
   .replace(/</g, '&lt;')
   .replace(/>/g, '&gt;')
   .replace(/"/g, '&quot;')
   .replace(/'/g, '&#39;')

const wrapMatches = (html: string) => {
  const term = parsedSearch.value.text
  if (!term || !html) return html || ''
  return html.replace(
    new RegExp(escapeRegex(term), 'gi'),
    (m: string) => `<mark class='bg-yellow-200'>${m}</mark>`
  )
}

const highlightText = (text: string) => wrapMatches(escapeHtml(text || ''))
const highlightHtml = (html: string) => wrapMatches(html || '')

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
        {{ $t('search.title') }}
      </h1>
      <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:flex-wrap">
        <p class="text-gray-500">
          <template v-if="searched">
            {{ $t('search.found_results_count', { count: total }) }}
          </template>
          <template v-else>
            {{ $t('search.results_for_label') }}
          </template>
          <span v-if="parsedSearch.text" class="font-semibold text-gray-900">"{{ parsedSearch.text }}"</span>
          <span v-else class="italic">{{ $t('search.all_items') }}</span>
        </p>
        <div v-if="parsedSearch.filters.length > 0" class="flex flex-wrap gap-2 mt-2 sm:mt-0 sm:ml-2">
          <span v-for="filter in parsedSearch.filters" :key="filter.key" class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 border border-blue-200">
            <span class="font-bold">{{ filter.key }}:</span> {{ filter.value }}
            <button @click="removeFilter(filter.fullMatch)" class="ml-1 text-blue-600 hover:text-blue-900 focus:outline-none">
              <XMarkIcon class="h-3 w-3" />
            </button>
          </span>
        </div>
      </div>
    </div>

    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 text-red-600 p-4 rounded-lg border border-red-200">
      {{ error }}
    </div>

    <div v-else-if="searched && matches.length === 0" class="text-center py-20 text-gray-500 bg-white rounded-lg border border-gray-100 shadow-sm">
      <MagnifyingGlassIcon class="mx-auto h-12 w-12 text-gray-300 mb-3" />
      <p class="text-lg mb-6">{{ $t('search.no_matches') }}</p>

      <div class="max-w-md mx-auto text-left bg-gray-50 p-4 rounded-lg border border-gray-200 text-sm">
        <h3 class="font-semibold text-gray-700 mb-2">{{ $t('search.tips_title') }}</h3>
        <ul class="list-disc pl-5 space-y-1">
           <li><code class="bg-gray-200 px-1 rounded text-gray-800">path:Law/</code> {{ $t('search.tip_path') }}</li>
           <li><code class="bg-gray-200 px-1 rounded text-gray-800">ext:djvu</code> {{ $t('search.tip_ext_or') }} <code class="bg-gray-200 px-1 rounded text-gray-800">ext:pdf</code> {{ $t('search.tip_ext') }}</li>
           <li>{{ $t('search.tip_combine') }} <code class="bg-gray-200 px-1 rounded text-gray-800">path:History/ ext:epub rome</code></li>
        </ul>
      </div>
    </div>

    <div v-else-if="matches.length > 0" class="bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden">
      <div v-if="totalPages > 1" class="px-4 py-3 bg-gray-50 border-b border-gray-100 flex items-center justify-between text-sm">
        <button
          @click="goToPage(currentPage - 1)"
          :disabled="currentPage <= 1"
          class="px-3 py-1.5 rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none"
        >
          ← {{ $t('search.previous') }}
        </button>
        <span class="text-gray-500">{{ $t('search.page_of', { page: currentPage, total: totalPages }) }}</span>
        <button
          @click="goToPage(currentPage + 1)"
          :disabled="currentPage >= totalPages"
          class="px-3 py-1.5 rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none"
        >
          {{ $t('search.next') }} →
        </button>
      </div>
      <ul class="divide-y divide-gray-100">
        <li v-for="match in matches" :key="match.path" class="hover:bg-gray-50 transition-colors p-4 group">
          <div class="relative flex gap-4">
            <!-- Icon/Cover -->
            <div class="flex-shrink-0">
               <div class="h-16 w-12 flex items-center justify-center bg-gray-100 rounded shadow-sm overflow-hidden border border-gray-200">
                 <img v-if="match.cover_url" :src="getFullUrl(match.cover_url)" class="w-full h-full object-contain" />
                 <DocumentIcon v-else class="h-6 w-6 text-gray-400" />
               </div>
            </div>

            <!-- Details -->
            <div class="flex-1 min-w-0 pr-12">
              <div class="flex items-start justify-between">
                <div>
                  <router-link :to="`/item/${match.path}`" class="text-lg font-medium text-blue-600 hover:underline break-words">
                    <span v-html="highlightText(match.title || formatFilename(match.name, match.is_dir))"></span>
                  </router-link>
                  <p v-if="match.author" class="text-sm text-gray-700 mt-0.5" :title="match.author" v-html="highlightText(match.author)"></p>
                  <p v-if="match.title" class="text-xs text-gray-500 mt-0.5 break-all">{{ match.name }}</p>
                  <p v-if="match.description" class="text-sm text-gray-600 mt-1 line-clamp-3" v-html="highlightHtml(match.description)"></p>
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
            <button v-if="match.hash_id" @click.prevent="toggleFavorite(match, $event)" class="absolute right-0 top-0 p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-blue-500': favoriteIds.has(match.hash_id), 'text-gray-400 hover:text-blue-500': !favoriteIds.has(match.hash_id) }" :title="favoriteIds.has(match.hash_id) ? $t('app.remove_favorite') : $t('app.add_favorite')">
              <BookmarkIconSolid v-if="favoriteIds.has(match.hash_id)" class="h-5 w-5" />
              <BookmarkIcon v-else class="h-5 w-5" />
            </button>
          </div>
        </li>
      </ul>
      <div v-if="totalPages > 1" class="px-4 py-3 bg-gray-50 border-t border-gray-100 flex items-center justify-between text-sm">
        <button
          @click="goToPage(currentPage - 1)"
          :disabled="currentPage <= 1"
          class="px-3 py-1.5 rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none"
        >
          ← {{ $t('search.previous') }}
        </button>
        <span class="text-gray-500">{{ $t('search.page_of', { page: currentPage, total: totalPages }) }}</span>
        <button
          @click="goToPage(currentPage + 1)"
          :disabled="currentPage >= totalPages"
          class="px-3 py-1.5 rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none"
        >
          {{ $t('search.next') }} →
        </button>
      </div>
    </div>
  </div>
</template>
