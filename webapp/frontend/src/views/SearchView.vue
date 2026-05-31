<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, computed, inject, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api, { startIntegrityJob, setBulkBookClearance, recommendBooksBulk, searchHashIds, getContainedKeys, type IntegrityMode } from '../api'
import AddToPlaylistPopover from '../components/AddToPlaylistPopover.vue'

const { t, locale } = useI18n({ useScope: 'global' })
const recTip = (it: any) => recommendedTooltip(t, locale.value, it?.recommended_by_name, it?.recommended_at)
import { DocumentIcon, MagnifyingGlassIcon, BookmarkIcon, ShieldCheckIcon, CheckCircleIcon, XMarkIcon as XMarkIconOutline, Squares2X2Icon, ListBulletIcon, FolderIcon, ArrowUpIcon, ArrowDownIcon, ScaleIcon, LockClosedIcon, CursorArrowRaysIcon, ArrowDownTrayIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid, XMarkIcon, CheckCircleIcon as CheckCircleIconSolid } from '@heroicons/vue/24/solid'
import StarRating from '../components/StarRating.vue'
import QualityMark from '../components/QualityMark.vue'
import { recommendedTooltip, bulkResultAlert } from '../lib/recommended'
import { gridItemSize, GRID_CLASSES, gridCls, estimateGridCols, roundToRowMultiple } from '../composables/useGridItemSize'
import { formatBytes, fileTypeLabel } from '../lib/itemFormat'
import { useEditClearance } from '../composables/useEditClearance'
import { getFullUrl } from '../lib/assets'

const { editClearance } = useEditClearance()
const route = useRoute()
const router = useRouter()
const matches = ref<any[]>([])
const loading = ref(false)
const searched = ref(false)
const error = ref('')
const favoriteIds = ref<Set<string>>(new Set())

const DEFAULT_PER_PAGE = 50
const currentUser = inject<Ref<{ email?: string, search_per_page?: number | null, is_admin?: boolean } | null>>(
  'currentUser',
  ref(null)
)

const selectMode = ref(false)
const selected = ref<Set<string>>(new Set())
const verifyStarting = ref(false)
const clearanceSaving = ref(false)
const recommendingBulk = ref(false)

const toggleSelectMode = () => {
  selectMode.value = !selectMode.value
  if (!selectMode.value) selected.value = new Set()
}

const onSelectModeKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Escape' && selectMode.value) {
    toggleSelectMode()
    return
  }
  if (
    e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey &&
    (e.key === 'm' || e.key === 'M') &&
    currentUser.value?.is_admin
  ) {
    e.preventDefault()
    toggleSelectMode()
  }
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

const startSelectionSetClearance = async () => {
  const ids = Array.from(selected.value)
  if (!ids.length) return
  const raw = window.prompt(t('admin.integrity.set_clearance_prompt', { count: ids.length }))
  if (raw === null) return
  const next = Number(raw)
  if (!Number.isFinite(next) || !Number.isInteger(next) || next < 0 || next > 100) {
    alert(t('admin.integrity.clearance_invalid_range'))
    return
  }
  clearanceSaving.value = true
  try {
    await setBulkBookClearance({ hash_ids: ids, clearance: next })
    const idSet = new Set(ids)
    for (const m of matches.value) {
      if (m.hash_id && idSet.has(m.hash_id)) m.clearance = next
    }
    clearSelection()
  } catch (err: any) {
    alert(err?.response?.data?.detail || err?.message || 'error')
  } finally {
    clearanceSaving.value = false
  }
}

const startSelectionRecommend = async () => {
  const ids = Array.from(selected.value)
  if (!ids.length) return
  recommendingBulk.value = true
  try {
    const res = await recommendBooksBulk(ids)
    const idSet = new Set(ids)
    for (const m of matches.value) {
      if (m.hash_id && idSet.has(m.hash_id)) m.is_recommended = true
    }
    clearSelection()
    bulkResultAlert(t, 'recommend', res.data)
  } catch (err: any) {
    alert(err?.response?.data?.detail || err?.message || 'error')
  } finally {
    recommendingBulk.value = false
  }
}

const editBookClearance = (match: any, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  editClearance(match)
}
const targetPerPage = computed(() => currentUser.value?.search_per_page ?? DEFAULT_PER_PAGE)
const total = ref(0)
const totalPages = ref(0)
const currentPage = computed(() => {
  const p = parseInt((route.query.page as string) || '1', 10)
  return isNaN(p) || p < 1 ? 1 : p
})

const savedSearchViewMode = localStorage.getItem('searchViewMode')
const viewMode = ref<'grid' | 'list'>(savedSearchViewMode === 'grid' ? 'grid' : 'list')
watch(viewMode, (v) => { localStorage.setItem('searchViewMode', v) })

// Debounced window width so the resize listener doesn't thrash effectivePerPage
// (which would refetch on every pixel of resize). We only care about crossing
// Tailwind breakpoints, which is rare enough that a 200ms debounce is plenty.
const windowWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1024)
let resizeTimer: number | null = null
const onResize = () => {
  if (resizeTimer !== null) window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(() => {
    windowWidth.value = window.innerWidth
  }, 200)
}

// Estimated column count for the current grid (1 outside grid mode). This is
// what we send as `cols` to /api/search so the backend can do directory-aware
// row-fill page-boundary adjustment.
const gridCols = computed(() => {
  if (viewMode.value !== 'grid') return 1
  return estimateGridCols(windowWidth.value, gridItemSize.value)
})

// In grid mode, round the user's preferred per_page to the nearest multiple of
// columns so the last row is never half-empty. In list mode, pass the user's
// preference through unchanged. Directory mode is special: the backend itself
// computes per-page boundaries that snap to whole-row endings of each group,
// so we just send the raw target and let the server decide.
const effectivePerPage = computed(() => {
  if (viewMode.value !== 'grid') return targetPerPage.value
  if (sortMode.value === 'directory') return targetPerPage.value
  return roundToRowMultiple(targetPerPage.value, gridCols.value)
})

const goToPage = (page: number) => {
  router.push({ name: 'search', query: { ...route.query, page: String(page) } })
}

type SortMode = 'relevance' | 'size' | 'directory'
type SortDir = 'asc' | 'desc'

const sortMode = computed<SortMode>(() => {
  const s = route.query.sort as string
  return s === 'size' || s === 'directory' ? s : 'relevance'
})
const sortDir = computed<SortDir>(() => (route.query.dir === 'asc' ? 'asc' : 'desc'))

// The direction toggle is only meaningful for size/directory; the UI hides it
// when relevance is active so users never see a no-op control.
const directionMeaningful = computed(() => sortMode.value !== 'relevance')

const setSort = (next: SortMode) => {
  if (next === sortMode.value) return
  const q: Record<string, string> = { ...(route.query as Record<string, string>), page: '1' }
  if (next === 'relevance') {
    delete q.sort
    delete q.dir
  } else {
    q.sort = next
    // Pick a sensible default direction the first time someone enters this
    // mode: desc for size (largest first — facsimiles), asc for directory
    // (alphabetical). Existing dir wins so the toggle isn't clobbered when
    // bouncing back to a mode the user already configured.
    if (!route.query.dir) q.dir = next === 'size' ? 'desc' : 'asc'
  }
  router.push({ name: 'search', query: q })
}

const toggleSortDir = () => {
  if (!directionMeaningful.value) return
  router.push({
    name: 'search',
    query: { ...(route.query as Record<string, string>), page: '1', dir: sortDir.value === 'asc' ? 'desc' : 'asc' },
  })
}

// Mirror the backend tokenizer (main.py `_SEARCH_TOKEN_RE`) so the chips and
// the "results for" term reflect exactly what the server parsed. A token is a
// structural filter ONLY when it captured an unquoted `field:` prefix — so a
// quoted whole token like "ext:Strangest" is a free-text phrase, never an ext
// chip. Multiple ext: values become multiple chips (OR'd server-side); path /
// needs_review keep the last (last-wins, like the backend).
const parsedSearch = computed(() => {
  const q = (route.query.q as string) || ''
  const isAdmin = !!currentUser.value?.is_admin
  const exts: {value: string, fullMatch: string}[] = []
  let pathF: {value: string, fullMatch: string} | null = null
  let nrF: {value: string, fullMatch: string} | null = null
  const residual: string[] = []
  const re = /(-)?(?:([A-Za-z_]+):)?(?:"([^"]*)"|'([^']*)'|([^\s"']+))/g
  let m: RegExpExecArray | null
  while ((m = re.exec(q)) !== null) {
    if (m[0] === '') { re.lastIndex++; continue }
    const neg = m[1] === '-'
    const field = (m[2] || '').toLowerCase()
    const value = m[3] ?? m[4] ?? m[5] ?? ''
    const structural = field === 'path' || field === 'ext' || (isAdmin && field === 'needs_review')
    if (structural && value) {
      if (field === 'ext') {
        if (!exts.some(e => e.value.toLowerCase() === value.toLowerCase())) exts.push({ value, fullMatch: m[0] })
      } else if (field === 'path') {
        pathF = { value, fullMatch: m[0] }
      } else {
        nrF = { value, fullMatch: m[0] }
      }
    } else {
      residual.push((neg ? '-' : '') + (field ? field + ':' : '') + value)
    }
  }

  const filters: {key: string, value: string, fullMatch: string}[] = []
  if (pathF) filters.push({ key: 'Path', value: pathF.value, fullMatch: pathF.fullMatch })
  for (const e of exts) filters.push({ key: 'Extension', value: e.value, fullMatch: e.fullMatch })
  if (nrF) filters.push({ key: 'Needs review', value: nrF.value, fullMatch: nrF.fullMatch })

  return { text: residual.join(' ').trim(), filters }
})

const removeFilter = (fullMatch: string) => {
  const currentQ = route.query.q as string || ''
  const newQ = currentQ.replace(fullMatch, '').replace(/\s+/g, ' ').trim()
  router.push({ name: 'search', query: { q: newQ } })
}

// `favoriteIds` now tracks which books sit in >=1 of the user's playlists —
// that drives the filled/blue bookmark state. Clicking the bookmark opens the
// add-to-playlist popover rather than toggling a single favourite.
const loadContainedKeys = async () => {
  if (!currentUser.value) {
    favoriteIds.value = new Set()
    return
  }
  try {
    const res = await getContainedKeys()
    favoriteIds.value = new Set(res.data.book_hash_ids)
  } catch (err) {
    console.error('Failed to load playlist membership', err)
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

// --- add-to-playlist popover ---
const popoverTarget = ref<{ book_hash_id?: string; dir_path?: string; title?: string } | null>(null)
const popoverPos = ref<{ top: number; left: number }>({ top: 0, left: 0 })

const toggleFavorite = (item: any, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!item.hash_id) return
  if (!requireAuth()) return
  const el = event.currentTarget as HTMLElement
  const rect = el.getBoundingClientRect()
  popoverPos.value = { top: rect.bottom + 4, left: rect.left }
  popoverTarget.value = { book_hash_id: item.hash_id, title: item.title || item.name }
}

const onMembershipChanged = () => { loadContainedKeys() }

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
    const params: Record<string, any> = {
      q, page, per_page: effectivePerPage.value, cols: gridCols.value,
    }
    if (sortMode.value !== 'relevance') {
      params.sort = sortMode.value
      params.dir = sortDir.value
    }
    const res = await api.get('/search', { params })
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
  loadContainedKeys()
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onSelectModeKeydown)
  doSearch(route.query.q as string, currentPage.value)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onSelectModeKeydown)
  if (resizeTimer !== null) window.clearTimeout(resizeTimer)
})

watch(() => [route.query.q, route.query.page, route.query.sort, route.query.dir], () => {
  doSearch(route.query.q as string, currentPage.value)
})

// Reset selection whenever the query text changes (paging preserves it).
watch(() => route.query.q, () => {
  selected.value = new Set()
})

// Effective per_page can change when: the user toggles list/grid, picks a new
// search_per_page in Settings, picks a new gridItemSize in Settings, or
// resizes across a Tailwind breakpoint (debounced). Any of these resets to
// page 1 and refetches. We also watch gridCols because in directory mode the
// backend recomputes boundaries from the column count, so a breakpoint cross
// shifts page edges even if effectivePerPage didn't change.
watch([effectivePerPage, gridCols], () => {
  if (!searched.value) return
  if (currentPage.value !== 1) {
    router.replace({ name: 'search', query: { ...route.query, page: '1' } })
  } else {
    doSearch(route.query.q as string, 1)
  }
})

// Re-sync when the signed-in identity changes. currentUser populates
// asynchronously in App.vue, so on a cold load / deep-link the bookmark fill
// state would otherwise stay empty until the user re-searches; and a logout
// while results are on screen would leave the previous user's filled bookmarks
// (and higher-clearance matches) visible. loadContainedKeys fixes the fill
// state; re-running the search refreshes the clearance-filtered results.
// Mirrors BrowseView's identity watcher. (Watch by email, not the ref — the
// /api/me heartbeat swaps the object every few minutes without a real change.)
watch(() => currentUser.value?.email ?? null, () => {
  if (currentUser.value) {
    loadContainedKeys()
  } else {
    favoriteIds.value = new Set()
  }
  if (searched.value) doSearch(route.query.q as string, currentPage.value)
})


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
    (m: string) => `<mark class='bg-yellow-200 dark:bg-yellow-700/60'>${m}</mark>`
  )
}

const highlightText = (text: string) => wrapMatches(escapeHtml(text || ''))
const highlightHtml = (html: string) => wrapMatches(html || '')

// Interleave directory-header markers with matches so list and grid views can
// share one v-for loop. In non-directory modes this is just the flat match
// list. Backend orders by symlink_path in directory mode, so same-parent
// items are already contiguous — one pass with a running counter suffices.
type FlatRow =
  | { kind: 'header'; dir: string; count: number }
  | { kind: 'match'; match: any }
const flatRows = computed<FlatRow[]>(() => {
  if (sortMode.value !== 'directory') {
    return matches.value.map(m => ({ kind: 'match', match: m }))
  }
  const rows: FlatRow[] = []
  let header: { kind: 'header'; dir: string; count: number } | null = null
  for (const m of matches.value) {
    if (!header || header.dir !== m.parent_dir) {
      header = { kind: 'header', dir: m.parent_dir, count: 0 }
      rows.push(header)
    }
    header.count++
    rows.push({ kind: 'match', match: m })
  }
  return rows
})

const rowKey = (row: FlatRow, idx: number): string =>
  row.kind === 'header' ? `h:${idx}:${row.dir}` : row.match.path

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
      <div class="flex items-center justify-between gap-2 mb-2">
        <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          <MagnifyingGlassIcon class="h-6 w-6 text-blue-600 dark:text-blue-400" />
          {{ $t('search.title') }}
        </h1>
        <div class="flex items-center gap-2 flex-wrap">
          <div class="inline-flex bg-gray-100 dark:bg-gray-800 p-1 rounded-lg" role="group" :aria-label="$t('search.sort_label')">
            <button
              @click="setSort('relevance')"
              :class="['px-2 py-1 rounded-md text-xs font-medium transition-colors flex items-center gap-1', sortMode === 'relevance' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']"
              :title="$t('search.sort_relevance')"
            >
              <MagnifyingGlassIcon class="h-4 w-4" />
              <span class="hidden sm:inline">{{ $t('search.sort_relevance') }}</span>
            </button>
            <button
              @click="setSort('size')"
              :class="['px-2 py-1 rounded-md text-xs font-medium transition-colors flex items-center gap-1', sortMode === 'size' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']"
              :title="$t('search.sort_size')"
            >
              <ScaleIcon class="h-4 w-4" />
              <span class="hidden sm:inline">{{ $t('search.sort_size') }}</span>
            </button>
            <button
              @click="setSort('directory')"
              :class="['px-2 py-1 rounded-md text-xs font-medium transition-colors flex items-center gap-1', sortMode === 'directory' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']"
              :title="$t('search.sort_directory')"
            >
              <FolderIcon class="h-4 w-4" />
              <span class="hidden sm:inline">{{ $t('search.sort_directory') }}</span>
            </button>
          </div>
          <button
            v-if="directionMeaningful"
            @click="toggleSortDir()"
            class="p-1.5 rounded-md transition-colors bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
            :title="sortDir === 'asc' ? $t('search.sort_asc') : $t('search.sort_desc')"
          >
            <ArrowUpIcon v-if="sortDir === 'asc'" class="h-4 w-4" />
            <ArrowDownIcon v-else class="h-4 w-4" />
          </button>
          <div class="inline-flex bg-gray-100 dark:bg-gray-800 p-1 rounded-lg">
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
          <button
            v-if="currentUser?.is_admin"
            @click="toggleSelectMode()"
            class="p-1.5 rounded-md transition-colors border text-sm font-medium flex items-center gap-1"
            :class="selectMode
              ? 'bg-emerald-100 text-emerald-700 border-emerald-300 hover:bg-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-200 dark:border-emerald-700 dark:hover:bg-emerald-900/50'
              : 'text-gray-500 dark:text-gray-400 border-transparent hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-gray-100 dark:hover:bg-gray-700'"
            :title="(selectMode ? t('admin.integrity.exit_select_mode') : t('admin.integrity.select_mode')) + ' (Ctrl+M)'"
          >
            <CursorArrowRaysIcon class="h-5 w-5" />
            <span class="hidden sm:inline">{{ t('admin.integrity.select_mode') }}</span>
          </button>
        </div>
      </div>
      <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:flex-wrap">
        <p class="text-gray-500 dark:text-gray-400">
          <template v-if="searched">
            {{ $t('search.found_results_count', { count: total }) }}
          </template>
          <template v-else>
            {{ $t('search.results_for_label') }}
          </template>
          <span v-if="parsedSearch.text" class="font-semibold text-gray-900 dark:text-gray-100">&nbsp;"{{ parsedSearch.text }}"</span>
          <span v-else class="italic">&nbsp;{{ $t('search.all_items') }}</span>
        </p>
        <div v-if="parsedSearch.filters.length > 0" class="flex flex-wrap gap-2 mt-2 sm:mt-0 sm:ml-2">
          <span v-for="filter in parsedSearch.filters" :key="filter.fullMatch" class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 border border-blue-200 dark:bg-blue-900/30 dark:text-blue-200 dark:border-blue-800">
            <span class="font-bold">{{ filter.key }}:</span> {{ filter.value }}
            <button @click="removeFilter(filter.fullMatch)" class="ml-1 text-blue-600 hover:text-blue-900 dark:text-blue-400 dark:hover:text-blue-200 focus:outline-none">
              <XMarkIcon class="h-3 w-3" />
            </button>
          </span>
        </div>
      </div>
    </div>

    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 text-red-600 p-4 rounded-lg border border-red-200 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800">
      {{ error }}
    </div>

    <div v-else-if="searched && matches.length === 0" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700 shadow-sm">
      <MagnifyingGlassIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
      <p class="text-lg mb-6">{{ $t('search.no_matches') }}</p>

      <div class="max-w-md mx-auto text-left bg-gray-50 dark:bg-gray-900 p-4 rounded-lg border border-gray-200 dark:border-gray-700 text-sm">
        <h3 class="font-semibold text-gray-700 dark:text-gray-300 mb-2">{{ $t('search.tips_title') }}</h3>
        <ul class="list-disc pl-5 space-y-1">
           <li><code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">harnum music</code> {{ $t('search.tip_words') }}</li>
           <li><code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">"music theory"</code> {{ $t('search.tip_phrase') }}</li>
           <li><code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">theor*</code> {{ $t('search.tip_wildcard') }}</li>
           <li><code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">-grammar</code> {{ $t('search.tip_exclude') }}</li>
           <li><code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">author:harnum</code> {{ $t('search.tip_field') }}</li>
           <li><code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">path:Law/</code> {{ $t('search.tip_path') }}</li>
           <li><code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">ext:djvu</code> {{ $t('search.tip_ext_or') }} <code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">ext:pdf</code> {{ $t('search.tip_ext') }}</li>
           <li>{{ $t('search.tip_combine') }} <code class="bg-gray-200 dark:bg-gray-700 px-1 rounded text-gray-800 dark:text-gray-200">path:History/ ext:epub rome</code></li>
        </ul>
      </div>
    </div>

    <div v-else-if="matches.length > 0" class="space-y-3">
      <div v-if="totalPages > 1" class="px-4 py-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700 shadow-sm flex items-center justify-between text-sm">
        <button
          @click="goToPage(currentPage - 1)"
          :disabled="currentPage <= 1"
          class="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none"
        >
          ← {{ $t('search.previous') }}
        </button>
        <span class="text-gray-500 dark:text-gray-400">{{ $t('search.page_of', { page: currentPage, total: totalPages }) }}</span>
        <button
          @click="goToPage(currentPage + 1)"
          :disabled="currentPage >= totalPages"
          class="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none"
        >
          {{ $t('search.next') }} →
        </button>
      </div>

      <!-- List View -->
      <ul v-if="viewMode === 'list'" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden divide-y divide-gray-100 dark:divide-gray-700">
        <template v-for="(row, idx) in flatRows" :key="rowKey(row, idx)">
          <li
            v-if="row.kind === 'header'"
            class="bg-gray-50 dark:bg-gray-900/60 px-4 py-2 flex items-center gap-2 text-sm sticky top-0 z-10"
          >
            <FolderIcon class="h-4 w-4 text-gray-500 dark:text-gray-400 flex-shrink-0" />
            <router-link
              :to="`/browse/${row.dir}`"
              class="font-mono text-gray-700 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:underline truncate"
            >/{{ row.dir || 'Root' }}</router-link>
            <span class="text-xs text-gray-500 dark:text-gray-400 ml-auto flex-shrink-0">
              {{ $t('search.group_items_count', { count: row.count }) }}
            </span>
          </li>
          <template v-else v-for="match in [row.match]" :key="match.path">
            <li
              class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors p-4 group"
              :class="selectMode && match.hash_id && isSelected(match.hash_id) ? 'bg-emerald-50 dark:bg-emerald-900/20' : ''"
              @click.capture="onItemClickCapture(match.hash_id, $event)"
            >
              <div class="relative flex gap-4">
                <!-- Selection indicator -->
                <div v-if="selectMode && match.hash_id" class="flex-shrink-0 self-center pointer-events-none">
                  <CheckCircleIconSolid v-if="isSelected(match.hash_id)" class="h-6 w-6 text-emerald-500" />
                  <CheckCircleIcon v-else class="h-6 w-6 text-gray-400 dark:text-gray-500" />
                </div>
                <!-- Icon/Cover -->
                <div class="flex-shrink-0">
                   <div class="h-16 w-12 flex items-center justify-center bg-gray-100 dark:bg-gray-900 rounded shadow-sm overflow-hidden border border-gray-200 dark:border-gray-700">
                     <img v-if="match.cover_url" :src="getFullUrl(match.cover_url)" class="w-full h-full object-contain" />
                     <DocumentIcon v-else class="h-6 w-6 text-gray-400 dark:text-gray-600" />
                   </div>
                </div>

                <!-- Details -->
                <div class="flex-1 min-w-0 pr-12">
                  <div class="flex items-start justify-between">
                    <div>
                      <router-link :to="`/item/${match.path}`" class="text-lg font-medium text-blue-600 dark:text-blue-400 hover:underline break-words">
                        <span v-html="highlightText(match.title || formatFilename(match.name, match.is_dir))"></span>
                      </router-link>
                      <router-link
                        v-if="match.author"
                        :to="{ name: 'search', query: { q: authorQuery(match.author) } }"
                        class="block text-sm text-gray-700 dark:text-gray-300 mt-0.5 hover:text-blue-600 dark:hover:text-blue-400 hover:underline"
                        :title="$t('search.search_by_author', { author: match.author })"
                      ><span v-html="highlightText(match.author)"></span></router-link>
                      <p v-if="match.title" class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 break-all">{{ match.name }}</p>
                      <p v-if="match.description" class="text-sm text-gray-600 dark:text-gray-400 mt-1 line-clamp-3" v-html="highlightHtml(match.description)"></p>
                    </div>
                  </div>

                  <div class="mt-2 text-xs text-gray-400 dark:text-gray-500 flex items-center gap-2 flex-wrap">
                     <QualityMark
                       v-if="match.is_recommended"
                       class="h-4 w-4 text-green-600 dark:text-green-400"
                       :title="recTip(match)"
                     />
                     <StarRating v-if="match.rating_count" :rating="match.avg_rating" :count="match.rating_count" />
                     <span v-if="match.rating_count" class="text-gray-300 dark:text-gray-600">·</span>
                     <span v-if="fileTypeLabel(match.name)" class="font-semibold">{{ fileTypeLabel(match.name) }}</span>
                     <span v-if="fileTypeLabel(match.name) && match.size != null">·</span>
                     <span v-if="match.size != null">{{ formatBytes(match.size) }}</span>
                     <span v-if="fileTypeLabel(match.name) || match.size != null" class="text-gray-300 dark:text-gray-600">·</span>
                     <span class="flex items-center gap-1">
                       {{ $t('app.location') }}:
                       <router-link :to="`/browse/${match.parent_dir}`" class="hover:text-blue-500 dark:hover:text-blue-400 hover:underline">
                         /{{ match.parent_dir || 'Root' }}
                       </router-link>
                     </span>
                  </div>
                </div>

                <!-- Bookmark Button -->
                <button v-if="match.hash_id" @click.prevent="toggleFavorite(match, $event)" class="absolute right-0 top-0 p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-blue-500': favoriteIds.has(match.hash_id), 'text-gray-400 hover:text-blue-500': !favoriteIds.has(match.hash_id) }" :title="$t('playlists.add_to')">
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
          </template>
        </template>
      </ul>

      <!-- Grid View -->
      <div v-if="viewMode === 'grid'" :class="['grid', GRID_CLASSES[gridItemSize]]">
        <template v-for="(row, idx) in flatRows" :key="rowKey(row, idx)">
          <div
            v-if="row.kind === 'header'"
            class="col-span-full flex items-center gap-2 px-2 py-1 mt-2 first:mt-0 border-b border-gray-200 dark:border-gray-700"
          >
            <FolderIcon class="h-4 w-4 text-gray-500 dark:text-gray-400 flex-shrink-0" />
            <router-link
              :to="`/browse/${row.dir}`"
              class="font-mono text-sm text-gray-700 dark:text-gray-300 hover:text-blue-600 dark:hover:text-blue-400 hover:underline truncate"
            >/{{ row.dir || 'Root' }}</router-link>
            <span class="text-xs text-gray-500 dark:text-gray-400 ml-auto flex-shrink-0">
              {{ $t('search.group_items_count', { count: row.count }) }}
            </span>
          </div>
          <template v-else v-for="match in [row.match]" :key="match.path">
          <div
            class="relative group"
            :class="selectMode && match.hash_id && isSelected(match.hash_id) ? 'ring-2 ring-emerald-500 rounded-xl' : ''"
            @click.capture="onItemClickCapture(match.hash_id, $event)"
          >
            <div
              v-if="selectMode && match.hash_id"
              class="absolute top-2 left-1/2 -translate-x-1/2 z-20 p-1 rounded-full bg-white/90 dark:bg-gray-900/90 border border-gray-200 dark:border-gray-700 shadow-sm pointer-events-none"
            >
              <CheckCircleIconSolid v-if="isSelected(match.hash_id)" class="h-6 w-6 text-emerald-500" />
              <CheckCircleIcon v-else class="h-6 w-6 text-gray-400 dark:text-gray-500" />
            </div>
            <span
              v-if="match.is_recommended"
              :class="['absolute top-2 left-2 z-10 inline-flex items-center justify-center rounded-full bg-white/80 dark:bg-gray-800/80 shadow-sm backdrop-blur-sm border border-gray-100 dark:border-gray-600', gridCls.iconBtn]"
              :title="recTip(match)"
            >
              <QualityMark :class="[gridCls.icon, 'text-green-600 dark:text-green-400']" />
            </span>
            <button v-if="match.hash_id" @click.prevent="toggleFavorite(match, $event)" :class="['absolute top-2 right-2 z-10 rounded-full bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-700 shadow-sm backdrop-blur-sm transition-colors border border-gray-100 dark:border-gray-600', gridCls.iconBtn, favoriteIds.has(match.hash_id) ? 'text-blue-500' : 'text-gray-400 hover:text-blue-500']" :title="$t('playlists.add_to')">
              <BookmarkIconSolid v-if="favoriteIds.has(match.hash_id)" :class="gridCls.icon" />
              <BookmarkIcon v-else :class="gridCls.icon" />
            </button>
            <button
              v-if="currentUser?.is_admin && match.hash_id"
              @click.prevent="editBookClearance(match, $event)"
              :class="['absolute top-2 left-1/2 -translate-x-1/2 z-10 rounded font-mono bg-amber-100 dark:bg-amber-900/70 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-700 hover:bg-amber-200 dark:hover:bg-amber-800', gridCls.badge]"
              :title="t('admin.edit_clearance_tooltip')"
            >🔒 {{ match.clearance ?? 0 }}</button>

            <router-link :to="`/item/${match.path}`" :class="['flex flex-col items-center bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 hover:shadow-md transition-all hover:border-green-300 dark:hover:border-green-500', gridCls.card]">
              <div :class="['aspect-[3/4] w-full rounded-lg overflow-hidden flex items-center justify-center bg-gray-50 dark:bg-gray-900 relative', gridCls.coverMargin]">
                <img v-if="match.cover_url" :src="getFullUrl(match.cover_url)" :alt="match.name" class="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300" />
                <DocumentIcon v-else :class="[gridCls.bigIcon, 'text-gray-300 dark:text-gray-600']" />
                <button
                  @click.prevent.stop="downloadItem(match, $event)"
                  :class="['absolute bottom-2 left-2 z-10 rounded-full bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-700 shadow-sm backdrop-blur-sm border border-gray-100 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:text-blue-500', gridCls.iconBtn]"
                  :title="$t('app.download')"
                >
                  <ArrowDownTrayIcon :class="gridCls.icon" />
                </button>
                <span
                  v-if="fileTypeLabel(match.name)"
                  :class="['absolute bottom-2 right-2 z-10 rounded font-mono font-semibold bg-gray-800/80 text-white backdrop-blur-sm', gridCls.badge]"
                >{{ fileTypeLabel(match.name) }}</span>
              </div>
              <h3 :class="[gridCls.title, 'font-medium text-gray-900 dark:text-gray-100 text-center w-full break-words line-clamp-2']" :title="match.title || match.name"><span v-html="highlightText(match.title || formatFilename(match.name, match.is_dir))"></span></h3>
              <p v-if="match.author" :class="[gridCls.subtitle, 'text-gray-500 dark:text-gray-400 mt-1 text-center w-full truncate font-bold italic']" :title="match.author"><span v-html="highlightText(match.author)"></span></p>
              <StarRating v-if="match.rating_count" :rating="match.avg_rating" :count="match.rating_count" class="mt-1" />
            </router-link>
          </div>
          </template>
        </template>
      </div>

      <div v-if="totalPages > 1" class="px-4 py-3 bg-white dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700 shadow-sm flex items-center justify-between text-sm">
        <button
          @click="goToPage(currentPage - 1)"
          :disabled="currentPage <= 1"
          class="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none"
        >
          ← {{ $t('search.previous') }}
        </button>
        <span class="text-gray-500 dark:text-gray-400">{{ $t('search.page_of', { page: currentPage, total: totalPages }) }}</span>
        <button
          @click="goToPage(currentPage + 1)"
          :disabled="currentPage >= totalPages"
          class="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none"
        >
          {{ $t('search.next') }} →
        </button>
      </div>
    </div>

    <!-- Bulk action bar for admin select mode -->
    <div
      v-if="selectMode"
      class="fixed bottom-4 inset-x-4 sm:inset-x-auto sm:right-4 z-40 flex flex-wrap items-center gap-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-lg px-4 py-3"
    >
      <span class="text-sm font-medium text-gray-700 dark:text-gray-300">
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
        @click="startSelectionSetClearance()"
        :disabled="!selected.size || clearanceSaving"
        class="px-3 py-1.5 rounded-lg text-sm font-medium bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
      >
        <LockClosedIcon class="h-4 w-4" />
        {{ t('admin.integrity.set_clearance_selected') }}
      </button>
      <!-- Search results aren't path-scoped, so the "Unrecommend selected"
           variant (BrowseView, only when inside /Recommended/) has no analogue
           here — search only offers Recommend. -->
      <button
        @click="startSelectionRecommend()"
        :disabled="!selected.size || recommendingBulk"
        class="px-3 py-1.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
      >
        <QualityMark class="h-4 w-4" />
        {{ t('admin.recommend_selected') }}
      </button>
      <button
        @click="clearSelection()"
        :disabled="!selected.size"
        class="px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
      >
        {{ t('admin.integrity.clear_selection') }}
      </button>
      <button
        @click="selectAllGlobal()"
        :disabled="!total || selectingAll"
        class="px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {{ t('admin.integrity.select_all_count', { count: total }) }}
      </button>
      <button
        @click="toggleSelectMode()"
        class="px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-1"
      >
        <XMarkIconOutline class="h-4 w-4" />
        {{ t('admin.integrity.exit_select_mode') }}
      </button>
    </div>

    <AddToPlaylistPopover
      v-if="popoverTarget"
      :position="popoverPos"
      :target="popoverTarget"
      @close="popoverTarget = null"
      @changed="onMembershipChanged"
    />
  </div>
</template>
