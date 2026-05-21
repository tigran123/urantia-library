<script setup lang="ts">
import { ref, onMounted, watch, computed, inject, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api, { startIntegrityJob, searchHashIds, type IntegrityMode } from '../api'

const { t } = useI18n({ useScope: 'global' })
import { DocumentIcon, MagnifyingGlassIcon, BookmarkIcon, ShieldCheckIcon, CheckCircleIcon, XMarkIcon as XMarkIconOutline } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid, XMarkIcon, CheckCircleIcon as CheckCircleIconSolid } from '@heroicons/vue/24/solid'
import StarRating from '../components/StarRating.vue'

const route = useRoute()
const router = useRouter()
const matches = ref<any[]>([])
const loading = ref(false)
const searched = ref(false)
const error = ref('')
const favoriteIds = ref<Set<string>>(new Set())

const DEFAULT_PER_PAGE = 50
const currentUser = inject<Ref<{ search_per_page?: number | null, is_admin?: boolean } | null>>(
  'currentUser',
  ref(null)
)

const selectMode = ref(false)
const selected = ref<Set<string>>(new Set())
const verifyStarting = ref(false)

const toggleSelectMode = () => {
  selectMode.value = !selectMode.value
  if (!selectMode.value) selected.value = new Set()
}

const isSelected = (hashId: string) => selected.value.has(hashId)

const toggleSelect = (hashId: string) => {
  const next = new Set(selected.value)
  if (next.has(hashId)) next.delete(hashId); else next.add(hashId)
  selected.value = next
}

const onItemClickCapture = (hashId: string | undefined, ev: Event) => {
  if (!selectMode.value || !hashId) return
  ev.preventDefault()
  ev.stopPropagation()
  toggleSelect(hashId)
}

const clearSelection = () => {
  selected.value = new Set()
}

const selectingAll = ref(false)
const selectAllGlobal = async () => {
  const q = (route.query.q as string) || ''
  if (!q) return
  selectingAll.value = true
  try {
    const res = await searchHashIds(q)
    selected.value = new Set(res.data.hash_ids)
  } catch (e: any) {
    alert(e?.response?.data?.detail || e?.message || 'error')
  } finally {
    selectingAll.value = false
  }
}

const startSelectionVerify = async (mode: IntegrityMode) => {
  const ids = Array.from(selected.value)
  if (!ids.length) return
  verifyStarting.value = true
  try {
    const res = await startIntegrityJob({ scope: 'hash_ids', hash_ids: ids, mode })
    router.push({ path: '/admin/integrity', query: { job: res.data.job_id } })
  } catch (e: any) {
    const detailMsg = e?.response?.data?.detail
    if (detailMsg && typeof detailMsg === 'object' && detailMsg.reason === 'job_running') {
      router.push({ path: '/admin/integrity', query: { job: detailMsg.running_job_id } })
    } else {
      alert(typeof detailMsg === 'string' ? detailMsg : (e?.message || 'error'))
    }
  } finally {
    verifyStarting.value = false
  }
}

const editBookClearance = async (match: any, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!match.hash_id) return
  const current = match.clearance ?? 0
  const raw = window.prompt(t('admin.clearance_prompt', { title: match.title || match.name }), String(current))
  if (raw === null) return
  const next = Number(raw)
  if (!Number.isFinite(next) || next < 0 || !Number.isInteger(next)) {
    alert(t('admin.clearance_invalid'))
    return
  }
  try {
    await api.put(`/admin/books/${encodeURIComponent(match.hash_id)}/clearance`, { clearance: next })
    match.clearance = next
  } catch (err: any) {
    alert(err.response?.data?.detail || err.message)
  }
}
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

  if (currentUser.value?.is_admin) {
    const nrMatch = text.match(/needs_review:(\S+)/)
    if (nrMatch) {
      filters.push({ key: 'Needs review', value: nrMatch[1].replace(/['"]/g, ''), fullMatch: nrMatch[0] })
      text = text.replace(nrMatch[0], '')
    }
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

// Guests may search freely, but a per-user action nudges them to sign in.
const requireAuth = (): boolean => {
  if (!currentUser.value) {
    router.push({ name: 'login' })
    return false
  }
  return true
}

const toggleFavorite = async (item: any, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!item.hash_id) return
  if (!requireAuth()) return
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

// Reset selection whenever the query text changes (paging preserves it).
watch(() => route.query.q, () => {
  selected.value = new Set()
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

// Build a scoped query that searches for one author, e.g. author:"Jane Doe".
const authorQuery = (author: string) => `author:"${(author || '').replace(/"/g, '')}"`

const escapeRegex = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;')
   .replace(/</g, '&lt;')
   .replace(/>/g, '&gt;')
   .replace(/"/g, '&quot;')
   .replace(/'/g, '&#39;')

// Positive search tokens to highlight: quoted phrases and bare words, with any
// `field:` prefix and `-`negation stripped (negated tokens are skipped).
const searchTokens = computed<string[]>(() => {
  const text = parsedSearch.value.text
  if (!text) return []
  const tokens: string[] = []
  const re = /(-)?(?:[A-Za-z_]+:)?(?:"([^"]*)"|'([^']*)'|(\S+))/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    if (m[1]) continue // negated term — nothing to highlight
    const val = (m[2] ?? m[3] ?? m[4] ?? '').trim()
    if (val && val.replace(/[*?]/g, '').trim()) tokens.push(val)
  }
  return tokens
})

// Turn one token into a regex fragment, mapping the `*`/`?` wildcards.
const tokenToRegex = (tok: string) =>
  escapeRegex(tok).replace(/\\\*/g, '\\S*').replace(/\\\?/g, '\\S')

const wrapMatches = (html: string) => {
  const tokens = searchTokens.value
  if (!tokens.length || !html) return html || ''
  const pattern = tokens.map(tokenToRegex).filter(Boolean).join('|')
  if (!pattern) return html
  return html.replace(
    new RegExp(pattern, 'gi'),
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
      <div class="flex items-center justify-between gap-2 mb-2">
        <h1 class="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <MagnifyingGlassIcon class="h-6 w-6 text-blue-600" />
          {{ $t('search.title') }}
        </h1>
        <button
          v-if="currentUser?.is_admin"
          @click="toggleSelectMode()"
          class="p-1.5 rounded-md transition-colors border text-sm font-medium flex items-center gap-1"
          :class="selectMode
            ? 'bg-emerald-100 text-emerald-700 border-emerald-300 hover:bg-emerald-200'
            : 'text-gray-500 border-transparent hover:text-emerald-600 hover:bg-gray-100'"
          :title="selectMode ? t('admin.integrity.exit_select_mode') : t('admin.integrity.select_mode')"
        >
          <ShieldCheckIcon class="h-5 w-5" />
          <span class="hidden sm:inline">{{ t('admin.integrity.select_mode') }}</span>
        </button>
      </div>
      <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:flex-wrap">
        <p class="text-gray-500">
          <template v-if="searched">
            {{ $t('search.found_results_count', { count: total }) }}
          </template>
          <template v-else>
            {{ $t('search.results_for_label') }}
          </template>
          <span v-if="parsedSearch.text" class="font-semibold text-gray-900">&nbsp;"{{ parsedSearch.text }}"</span>
          <span v-else class="italic">&nbsp;{{ $t('search.all_items') }}</span>
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
           <li><code class="bg-gray-200 px-1 rounded text-gray-800">harnum music</code> {{ $t('search.tip_words') }}</li>
           <li><code class="bg-gray-200 px-1 rounded text-gray-800">"music theory"</code> {{ $t('search.tip_phrase') }}</li>
           <li><code class="bg-gray-200 px-1 rounded text-gray-800">theor*</code> {{ $t('search.tip_wildcard') }}</li>
           <li><code class="bg-gray-200 px-1 rounded text-gray-800">-grammar</code> {{ $t('search.tip_exclude') }}</li>
           <li><code class="bg-gray-200 px-1 rounded text-gray-800">author:harnum</code> {{ $t('search.tip_field') }}</li>
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
        <li
          v-for="match in matches"
          :key="match.path"
          class="hover:bg-gray-50 transition-colors p-4 group"
          :class="selectMode && match.hash_id && isSelected(match.hash_id) ? 'bg-emerald-50' : ''"
          @click.capture="onItemClickCapture(match.hash_id, $event)"
        >
          <div class="relative flex gap-4">
            <!-- Selection indicator -->
            <div v-if="selectMode && match.hash_id" class="flex-shrink-0 self-center pointer-events-none">
              <CheckCircleIconSolid v-if="isSelected(match.hash_id)" class="h-6 w-6 text-emerald-500" />
              <CheckCircleIcon v-else class="h-6 w-6 text-gray-400" />
            </div>
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
                  <router-link
                    v-if="match.author"
                    :to="{ name: 'search', query: { q: authorQuery(match.author) } }"
                    class="block text-sm text-gray-700 mt-0.5 hover:text-blue-600 hover:underline"
                    :title="$t('search.search_by_author', { author: match.author })"
                  ><span v-html="highlightText(match.author)"></span></router-link>
                  <p v-if="match.title" class="text-xs text-gray-500 mt-0.5 break-all">{{ match.name }}</p>
                  <p v-if="match.description" class="text-sm text-gray-600 mt-1 line-clamp-3" v-html="highlightHtml(match.description)"></p>
                </div>
              </div>

              <div class="mt-2 text-xs text-gray-400 flex items-center gap-2 flex-wrap">
                 <StarRating v-if="match.rating_count" :rating="match.avg_rating" :count="match.rating_count" />
                 <span v-if="match.rating_count" class="text-gray-300">·</span>
                 <span class="flex items-center gap-1">
                   {{ $t('app.location') }}
                   <router-link :to="`/browse/${match.parent_dir}`" class="hover:text-blue-500 hover:underline">
                     /{{ match.parent_dir || 'Root' }}
                   </router-link>
                 </span>
              </div>
            </div>

            <!-- Bookmark Button -->
            <button v-if="match.hash_id" @click.prevent="toggleFavorite(match, $event)" class="absolute right-0 top-0 p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-blue-500': favoriteIds.has(match.hash_id), 'text-gray-400 hover:text-blue-500': !favoriteIds.has(match.hash_id) }" :title="favoriteIds.has(match.hash_id) ? $t('app.remove_favorite') : $t('app.add_favorite')">
              <BookmarkIconSolid v-if="favoriteIds.has(match.hash_id)" class="h-5 w-5" />
              <BookmarkIcon v-else class="h-5 w-5" />
            </button>
            <button
              v-if="currentUser?.is_admin && match.hash_id"
              @click.prevent="editBookClearance(match, $event)"
              class="absolute right-10 top-1 px-1.5 py-0.5 rounded text-xs font-mono bg-amber-100 dark:bg-amber-900/70 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-700 hover:bg-amber-200 dark:hover:bg-amber-800"
              :title="t('admin.edit_clearance_tooltip')"
            >🔒 {{ match.clearance ?? 0 }}</button>
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

    <!-- Bulk action bar for admin select mode -->
    <div
      v-if="selectMode"
      class="fixed bottom-4 inset-x-4 sm:inset-x-auto sm:right-4 z-40 flex flex-wrap items-center gap-3 bg-white border border-gray-200 rounded-xl shadow-lg px-4 py-3"
    >
      <span class="text-sm font-medium text-gray-700">
        {{ t('admin.integrity.selected_count', { count: selected.size }) }}
      </span>
      <button
        @click="startSelectionVerify('quick')"
        :disabled="!selected.size || verifyStarting"
        class="px-3 py-1.5 rounded-lg text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
      >
        <ShieldCheckIcon class="h-4 w-4" />
        {{ t('admin.integrity.verify_selected') }} — {{ t('admin.integrity.quick') }}
      </button>
      <button
        @click="startSelectionVerify('full')"
        :disabled="!selected.size || verifyStarting"
        class="px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
      >
        <ShieldCheckIcon class="h-4 w-4" />
        {{ t('admin.integrity.verify_selected') }} — {{ t('admin.integrity.full') }}
      </button>
      <button
        @click="clearSelection()"
        :disabled="!selected.size"
        class="px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
      >
        {{ t('admin.integrity.clear_selection') }}
      </button>
      <button
        @click="selectAllGlobal()"
        :disabled="!total || selectingAll"
        class="px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {{ t('admin.integrity.select_all_count', { count: total }) }}
      </button>
      <button
        @click="toggleSelectMode()"
        class="px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 flex items-center gap-1"
      >
        <XMarkIconOutline class="h-4 w-4" />
        {{ t('admin.integrity.exit_select_mode') }}
      </button>
    </div>
  </div>
</template>
