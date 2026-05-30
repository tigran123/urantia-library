<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted, defineAsyncComponent, inject, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api, {
  verifyBook,
  getMyRating, setMyRating,
  getComments, postComment, editComment, deleteComment,
  recommendBook, unrecommendBook,
  getContainedKeys,
  type IntegrityCheckResult, type IntegrityMode, type CommentNode,
} from '../api'
import { getFullUrl } from '../lib/assets'
import { DocumentIcon, ArrowDownTrayIcon, BookmarkIcon, PencilSquareIcon, ShieldCheckIcon, XMarkIcon, CheckCircleIcon, XCircleIcon, FlagIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'
import StarRating from '../components/StarRating.vue'
import QualityMark from '../components/QualityMark.vue'
import { recommendedTooltip, recommendedByValue } from '../lib/recommended'
import { useI18n } from 'vue-i18n'
import DjvuViewer from '../components/DjvuViewer.vue'
import EpubViewer from '../components/EpubViewer.vue'
import ImageViewer from '../components/ImageViewer.vue'
import Fb2Viewer from '../components/Fb2Viewer.vue'
import MdViewer from '../components/MdViewer.vue'
import HtmlViewer from '../components/HtmlViewer.vue'
import BookMetadataEditor from '../components/BookMetadataEditor.vue'
import AddToPlaylistPopover from '../components/AddToPlaylistPopover.vue'
// pdfjs-dist is ~1MB; keep it out of the main bundle by lazy-loading.
const PdfViewer = defineAsyncComponent(() => import('../components/PdfViewer.vue'))

const { t, locale } = useI18n({ useScope: 'global' })
const recTip = (it: any) => recommendedTooltip(t, locale.value, it?.recommended_by_name, it?.recommended_at)
const recByValue = (it: any) => recommendedByValue(t, locale.value, it?.recommended_by_name, it?.recommended_at)
const route = useRoute()
const router = useRouter()
const currentUser = inject<Ref<{ email?: string, is_admin?: boolean } | null>>('currentUser', ref(null))
const item = ref<any>(null)
const loading = ref(true)
const error = ref('')
const currentPath = ref('')
const originalTitle = ref(document.title)
const favoriteIds = ref<Set<string>>(new Set())
const editingId = ref<string | null>(null)

const openEditor = () => {
  if (item.value?.hash_id) editingId.value = item.value.hash_id
}

const verifyOpen = ref(false)
const verifying = ref(false)
const verifyResult = ref<IntegrityCheckResult | null>(null)
const verifyError = ref('')

const openVerify = () => {
  verifyResult.value = null
  verifyError.value = ''
  verifyOpen.value = true
}

const closeVerify = () => {
  verifyOpen.value = false
}

const runVerify = async (mode: IntegrityMode) => {
  if (!item.value?.hash_id) return
  verifying.value = true
  verifyError.value = ''
  try {
    const res = await verifyBook(item.value.hash_id, mode)
    verifyResult.value = res.data
    // Reflect the new last_verified_* on the item so the metadata row updates.
    item.value.last_verified_at = res.data.verified_at
    item.value.last_verified_ok = res.data.ok
    item.value.last_verified_mode = res.data.mode
    item.value.last_verified_error = res.data.error
  } catch (e: any) {
    verifyError.value = e?.response?.data?.detail || e?.message || 'verification failed'
  } finally {
    verifying.value = false
  }
}

const verifyErrorLabel = (key: string | null | undefined): string => {
  if (!key) return ''
  const msg = t(`admin.integrity.error.${key}`)
  // i18n returns the key itself when no translation; fall back to the raw key.
  return msg.startsWith('admin.integrity.error.') ? key : msg
}

const checkLabel = (name: string): string => {
  const known: Record<string, string> = {
    db_row: 'check_db_row',
    data_file_exists: 'check_data_file_exists',
    data_file_size: 'check_data_file_size',
    locations_present: 'check_locations_present',
    symlinks_resolve: 'check_symlinks_resolve',
    hash_match: 'check_hash_match',
  }
  return known[name] ? t(`admin.integrity.${known[name]}`) : name
}

const formatScalarDetail = (name: string, detail: any): string => {
  if (detail === null || detail === undefined) return ''
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object') {
    if (name === 'data_file_size' && typeof detail.bytes === 'number') {
      return t('admin.integrity.detail_bytes', { count: detail.bytes })
    }
    if (name === 'locations_present' && typeof detail.count === 'number') {
      return t('admin.integrity.detail_locations', { count: detail.count })
    }
  }
  return ''
}

const onEditorSaved = (updated: any) => {
  if (!item.value || item.value.hash_id !== updated.id) return
  item.value.title = updated.title
  item.value.author = updated.author
  item.value.publisher = updated.publisher
  item.value.published = updated.published
  item.value.description = updated.description
  item.value.tags = updated.tags
  item.value.series = updated.series
  item.value.languages = updated.languages
  item.value.identifiers = updated.identifiers
  item.value.clearance = updated.clearance
  if (updated.title) document.title = updated.title
  // The editor can rename the file; if our current path is no longer one of
  // the book's locations, navigate to the new path so the URL stops 404ing
  // on reload and the filename caption refreshes.
  if (Array.isArray(updated.locations) && !updated.locations.includes(currentPath.value)) {
    const parent = currentPath.value.split('/').slice(0, -1).join('/')
    const newPath = updated.locations.find((l: string) => l.startsWith(parent + '/'))
      || updated.locations[0]
    if (newPath && newPath !== currentPath.value) {
      router.replace(`/item/${newPath}`)
    }
  }
}

const onEditorDeleted = () => {
  // The book this page shows is gone. If we got here from a playlist (see the
  // ?from= tag PlaylistDetailView attaches), bounce back to that playlist;
  // otherwise drop into the parent directory.
  const from = typeof route.query.from === 'string' ? route.query.from : ''
  if (from.startsWith('playlist:')) {
    const pid = from.slice('playlist:'.length)
    if (pid && /^\d+$/.test(pid)) {
      router.replace(`/playlists/${pid}`)
      return
    }
  }
  const parent = currentPath.value.split('/').slice(0, -1).join('/')
  router.replace(`/browse/${parent}`)
}

// `favoriteIds` tracks which books sit in >=1 of the user's playlists — drives
// the filled/blue bookmark. The bookmark opens the add-to-playlist popover
// (same as Browse/Search), replacing the dead single-favourite store.
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

// Guests may read freely, but a per-user action nudges them to sign in.
const requireAuth = (): boolean => {
  if (!currentUser.value) {
    router.push({ name: 'login' })
    return false
  }
  return true
}

const toggleRecommend = async () => {
  if (!item.value || !item.value.hash_id) return
  if (!currentUser.value?.is_admin) return
  const id = item.value.hash_id
  try {
    if (item.value.is_recommended) {
      const res = await unrecommendBook(id)
      const removed = new Set(res.data.removed || [])
      item.value.is_recommended = false
      item.value.recommended_at = null
      item.value.recommended_by_name = null
      if (Array.isArray(item.value.locations)) {
        item.value.locations = item.value.locations.filter((p: string) => !removed.has(p))
      }
    } else {
      const res = await recommendBook(id)
      item.value.is_recommended = true
      item.value.recommended_at = res.data.recommended_at
      item.value.recommended_by_name = res.data.recommended_by_name
      if (res.data.symlink_path) {
        const next = Array.isArray(item.value.locations) ? [...item.value.locations] : []
        if (!next.includes(res.data.symlink_path)) next.push(res.data.symlink_path)
        next.sort()
        item.value.locations = next
      }
    }
  } catch (err) {
    console.error('Failed to toggle recommendation', err)
  }
}

// --- add-to-playlist popover ---
const popoverTarget = ref<{ book_hash_id?: string; dir_path?: string; title?: string } | null>(null)
const popoverPos = ref<{ top: number; left: number }>({ top: 0, left: 0 })

const openPlaylistPopover = (event: Event) => {
  if (!item.value || !item.value.hash_id) return
  if (!requireAuth()) return
  const el = event.currentTarget as HTMLElement
  const rect = el.getBoundingClientRect()
  popoverPos.value = { top: rect.bottom + 4, left: rect.left }
  popoverTarget.value = { book_hash_id: item.value.hash_id, title: item.value.title || item.value.name }
}

const onMembershipChanged = () => { loadContainedKeys() }

const loadItem = async (path: string) => {
  loading.value = true
  error.value = ''
  try {
    const p = Array.isArray(path) ? path.join('/') : path || ''
    currentPath.value = p
    
    // We need to fetch the file details. We can use the /browse API on the parent directory
    // and find the specific file.
    const parts = p.split('/')
    const fileName = parts.pop()
    const parentPath = parts.join('/')
    
    const res = await api.get('/browse', { params: { path: parentPath } })
    const foundItem = res.data.items.find((i: any) => i.name === fileName)
    
    if (foundItem) {
      item.value = foundItem
      document.title = foundItem.name.replace(/\.[^/.]+$/, "")
    } else {
      error.value = 'Item not found'
    }
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  originalTitle.value = document.title
  loadContainedKeys()
  loadItem(route.params.path as string)
})

onUnmounted(() => {
  document.title = originalTitle.value
})

watch(() => route.params.path, (newPath) => {
  loadItem(newPath as string)
})

// Re-sync the bookmark fill state when the signed-in identity changes.
// currentUser populates asynchronously in App.vue, so a cold load / deep-link
// to /item/:path while logged in would leave the bookmark hollow until the
// user opens the popover; a logout would leave a stale filled bookmark.
// Watch by email (not the ref) so the /api/me heartbeat's object swap is a
// no-op. Mirrors BrowseView/SearchView.
watch(() => currentUser.value?.email ?? null, () => {
  if (currentUser.value) loadContainedKeys()
  else favoriteIds.value = new Set()
})

const getDownloadUrl = () => {
  if (!item.value) return ''
  return getFullUrl(`/api/files/${item.value.path.split('/').map(encodeURIComponent).join('/')}`)
}

const formatBytes = (bytes: number, decimals = 2) => {
    if (!+bytes) return `0 ${t('app.bytes')}`
    const k = 1024
    const dm = decimals < 0 ? 0 : decimals
    const sizes = [t('app.bytes'), 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const formatDate = (dateString: string) => {
  if (!dateString) return t('app.unknown')
  return new Date(dateString).toLocaleString()
}

const fileExtension = computed(() => {
  if (!item.value || !item.value.name) return ''
  const parts = item.value.name.split('.')
  return parts.length > 1 ? parts.pop().toLowerCase() : ''
})

const isAudio = computed(() => ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac'].includes(fileExtension.value))
const isVideo = computed(() => ['mp4', 'webm', 'mkv', 'avi', 'mov'].includes(fileExtension.value))
const isImage = computed(() => ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'].includes(fileExtension.value))
const isPdf = computed(() => fileExtension.value === 'pdf')
const isDjvu = computed(() => fileExtension.value === 'djvu')
const isEpub = computed(() => fileExtension.value === 'epub')
const isFb2 = computed(() => {
  const n = (item.value?.name || '').toLowerCase()
  return n.endsWith('.fb2') || n.endsWith('.fb2.zip')
})
const isMd = computed(() => ['md', 'markdown'].includes(fileExtension.value))
const isTxt = computed(() => fileExtension.value === 'txt')
const isCode = computed(() => ['py', 'c', 'cpp', 'h', 'hpp', 'js', 'ts', 'jsx', 'tsx', 'lua', 'sh', 'bash', 'rs', 'go', 'java', 'css', 'scss', 'json', 'xml', 'yaml', 'yml', 'sql', 'ini'].includes(fileExtension.value))
const isHtml = computed(() => {
  const n = (item.value?.name || '').toLowerCase()
  return n.endsWith('.html') || n.endsWith('.htm')
      || n.endsWith('.html.zip') || n.endsWith('.htm.zip')
})

const displayFormat = computed(() => {
  if (isFb2.value) return 'FB2'
  if (isMd.value) return 'Markdown'
  if (isHtml.value) return 'HTML'
  if (isCode.value) return t('app.source_code')
  return fileExtension.value
})

// Stable source object for the built-in viewers. It MUST be a computed (not an
// inline object literal in the template): the viewers watch `source` and
// reload the whole book when its reference changes, so an inline literal would
// make every ItemView re-render (e.g. each keystroke in the comment box)
// reload — or, for EPUB, destroy — the open book.
const viewerSource = computed(() => ({
  kind: 'live' as const,
  path: item.value?.path ?? '',
  hashId: item.value?.hash_id ?? '',
}))

const textPreview = ref<{ text: string; html: string }>({ text: '', html: '' })

const loadTextPreview = async (path: string) => {
  textPreview.value = { text: '', html: '' }
  try {
    const res = await api.get('/text-preview', { params: { path, max_chars: 1500 } })
    textPreview.value = {
      text: res.data.text || '',
      html: res.data.html || '',
    }
  } catch (e) {
    console.error('Failed to load text preview', e)
  }
}

watch(
  () => item.value && (isMd.value || isTxt.value || isCode.value) ? item.value.path : null,
  (p) => { if (p) loadTextPreview(p); else textPreview.value = { text: '', html: '' } },
  { immediate: true }
)

const htmlPreview = ref<{ html: string }>({ html: '' })

const loadHtmlPreview = async (path: string) => {
  htmlPreview.value = { html: '' }
  try {
    const res = await api.get('/html-preview', { params: { path, max_chars: 1500 } })
    htmlPreview.value = { html: res.data.html || '' }
  } catch (e) {
    console.error('Failed to load HTML preview', e)
  }
}

watch(
  () => item.value && isHtml.value ? item.value.path : null,
  (p) => { if (p) loadHtmlPreview(p); else htmlPreview.value = { html: '' } },
  { immediate: true }
)

// ---------- Ratings & comments ----------
const myRating = ref<number | null>(null)
const comments = ref<CommentNode[]>([])
const reviewDraft = ref('')
const reviewBusy = ref(false)
const editingCommentId = ref<number | null>(null)
const editDraft = ref('')
const replyToId = ref<number | null>(null)
const replyDraft = ref('')

// The caller's own top-level comment. Used to decide whether to show the
// new-comment textarea; the comment itself is rendered inline in the list.
const myComment = computed<CommentNode | null>(
  () => comments.value.find((c) => c.is_own) || null)

const loadRatingAndComments = async (hashId: string) => {
  myRating.value = null
  comments.value = []
  try {
    const [r, c] = await Promise.all([getMyRating(hashId), getComments(hashId)])
    myRating.value = r.data.rating
    comments.value = c.data.comments
  } catch (e) {
    console.error('Failed to load ratings/comments', e)
  }
}

watch(
  () => item.value?.hash_id || null,
  (h) => { if (h) loadRatingAndComments(h) },
  { immediate: true }
)

const onRate = async (value: number) => {
  if (!item.value?.hash_id) return
  if (!requireAuth()) return
  try {
    await setMyRating(item.value.hash_id, value)
    // Fold the change into the displayed average without a full reload.
    const oldCount = item.value.rating_count || 0
    const oldAvg = item.value.avg_rating || 0
    let total = oldAvg * oldCount
    let count = oldCount
    if (myRating.value != null) total -= myRating.value
    else count += 1
    total += value
    item.value.rating_count = count
    item.value.avg_rating = count ? total / count : null
    myRating.value = value
  } catch (e) {
    console.error('Failed to set rating', e)
  }
}

const submitReview = async () => {
  if (!item.value?.hash_id) return
  const body = reviewDraft.value.trim()
  if (!body) return
  reviewBusy.value = true
  try {
    await postComment(item.value.hash_id, body)
    reviewDraft.value = ''
    await loadRatingAndComments(item.value.hash_id)
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to post comment')
  } finally {
    reviewBusy.value = false
  }
}

const startEdit = (c: CommentNode) => {
  editingCommentId.value = c.id
  editDraft.value = c.body
}

const saveEdit = async () => {
  if (editingCommentId.value == null) return
  const body = editDraft.value.trim()
  if (!body) return
  reviewBusy.value = true
  try {
    await editComment(editingCommentId.value, body)
    editingCommentId.value = null
    if (item.value?.hash_id) await loadRatingAndComments(item.value.hash_id)
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to save')
  } finally {
    reviewBusy.value = false
  }
}

const removeComment = async (c: CommentNode) => {
  if (!confirm(t('app.delete_comment_confirm'))) return
  try {
    await deleteComment(c.id)
    if (item.value?.hash_id) await loadRatingAndComments(item.value.hash_id)
  } catch (e) {
    console.error('Failed to delete comment', e)
  }
}

const startReply = (c: CommentNode) => {
  replyToId.value = c.id
  replyDraft.value = ''
}

const submitReply = async () => {
  if (!item.value?.hash_id || replyToId.value == null) return
  const body = replyDraft.value.trim()
  if (!body) return
  reviewBusy.value = true
  try {
    await postComment(item.value.hash_id, body, replyToId.value)
    replyToId.value = null
    replyDraft.value = ''
    await loadRatingAndComments(item.value.hash_id)
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to post reply')
  } finally {
    reviewBusy.value = false
  }
}
</script>

<template>
  <div class="w-full px-2 py-4 md:p-6">
    <div v-if="loading" class="flex justify-center py-12">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
    </div>
    
    <div v-else-if="error" class="bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 p-4 rounded-lg text-center">
      {{ error }}
    </div>
    
    <div v-else-if="item" class="space-y-8 transition-colors duration-300 bg-gray-50 dark:bg-gray-900 min-h-screen rounded-xl pb-12 pt-8">
      
      <!-- Main Content Grid -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-8 px-2 md:px-8">
        
        <!-- Left Column: Cover Image -->
        <div class="md:col-span-1 flex flex-col items-center">
          <div class="w-full max-w-sm aspect-[3/4] rounded-lg shadow-xl overflow-hidden bg-white dark:bg-gray-800 flex items-center justify-center border border-gray-200 dark:border-gray-700 relative">
            <QualityMark
              v-if="item.is_recommended"
              class="absolute top-3 left-3 z-10 h-8 w-8 text-green-600 dark:text-green-400 drop-shadow"
              :title="recTip(item)"
            />
            <img v-if="item.cover_url" :src="getFullUrl(item.cover_url)" :alt="item.name" class="w-full h-full object-contain" />
            <template v-else-if="(isMd || isTxt || isCode) && (textPreview.html || textPreview.text)">
              <div
                v-if="textPreview.html"
                class="md-content md-content--preview absolute inset-0 m-0 px-3 py-3 text-[10px] leading-snug overflow-hidden text-gray-700 dark:text-gray-300"
                v-html="textPreview.html"
              ></div>
              <pre
                v-else
                class="absolute inset-0 m-0 px-3 py-3 text-[10px] leading-snug whitespace-pre-wrap break-words overflow-hidden text-gray-700 dark:text-gray-300 font-serif"
              >{{ textPreview.text }}</pre>
              <div class="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-white dark:from-gray-800 to-transparent pointer-events-none"></div>
            </template>
            <template v-else-if="isHtml && htmlPreview.html">
              <div
                class="html-content html-content--preview absolute inset-0 m-0 px-3 py-3 text-[10px] leading-snug overflow-hidden text-gray-700 dark:text-gray-300"
                v-html="htmlPreview.html"
              ></div>
              <div class="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-white dark:from-gray-800 to-transparent pointer-events-none"></div>
            </template>
            <DocumentIcon v-else class="w-32 h-32 text-gray-300 dark:text-gray-600" />
          </div>
          <div v-if="item.hash_id && item.rating_count" class="mt-3 flex justify-center">
            <StarRating :rating="item.avg_rating" :count="item.rating_count" size="h-5 w-5" />
          </div>
          <div v-if="item.hash_id" class="mt-3 flex items-center justify-center gap-1">
            <button
              v-if="currentUser?.is_admin"
              @click.prevent="openEditor()"
              class="p-2 rounded-full text-gray-400 hover:text-blue-500 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
              :title="t('admin.edit_book')"
            >
              <PencilSquareIcon class="h-7 w-7" />
            </button>
            <button
              v-if="currentUser?.is_admin"
              @click.prevent="openVerify()"
              class="p-2 rounded-full text-gray-400 hover:text-emerald-500 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
              :title="t('admin.integrity.verify_tooltip')"
            >
              <ShieldCheckIcon class="h-7 w-7" />
            </button>
            <button
              v-if="currentUser?.is_admin"
              @click.prevent="toggleRecommend()"
              class="p-2 rounded-full transition-colors hover:bg-gray-200 dark:hover:bg-gray-700"
              :class="item.is_recommended
                ? 'text-green-600 dark:text-green-400'
                : 'text-gray-400 hover:text-green-600 dark:hover:text-green-400'"
              :title="item.is_recommended ? t('app.unrecommend') : t('app.recommend')"
            >
              <QualityMark class="h-7 w-7" />
            </button>
            <button @click.prevent="openPlaylistPopover($event)" class="p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-blue-500': favoriteIds.has(item.hash_id), 'text-gray-400 hover:text-blue-500': !favoriteIds.has(item.hash_id) }" :title="t('playlists.add_to')">
              <BookmarkIconSolid v-if="favoriteIds.has(item.hash_id)" class="h-7 w-7" />
              <BookmarkIcon v-else class="h-7 w-7" />
            </button>
            <router-link
              v-if="currentUser"
              :to="{
                name: 'feedback-compose',
                query: {
                  book: item.hash_id,
                  cat: 'book',
                  book_title: item.title || item.name,
                  book_author: item.author || '',
                },
              }"
              class="p-2 rounded-full text-gray-400 hover:text-amber-500 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
              :title="t('feedback.report')"
            >
              <FlagIcon class="h-7 w-7" />
            </router-link>
          </div>
        </div>

        <!-- Right Column: Metadata & Actions -->
        <div class="md:col-span-2 flex flex-col justify-center space-y-6">
          <!-- Details (collapsible metadata panel; expanded by default) -->
          <details open class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm group overflow-hidden">
            <summary class="px-6 py-4 cursor-pointer font-medium text-gray-900 dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-700/50 list-none flex items-center justify-between">
              <span>{{ t('app.details') }}</span>
              <span class="transition-transform group-open:rotate-90 text-gray-400">›</span>
            </summary>
            <div v-if="item.description" class="px-6 py-4 border-t border-gray-200 dark:border-gray-700">
              <div class="font-medium text-gray-900 dark:text-gray-100 mb-2">{{ t('app.description') }}</div>
              <div class="prose dark:prose-invert max-w-none text-gray-700 dark:text-gray-300 whitespace-pre-wrap" v-html="item.description"></div>
            </div>
            <table class="w-full text-sm text-left border-t border-gray-200 dark:border-gray-700">
              <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                <tr v-if="item.title" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.book_title') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ item.title }}</td>
                </tr>
                <tr v-if="item.author" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.author') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ item.author }}</td>
                </tr>
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.format') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400 uppercase font-semibold">{{ displayFormat }}</td>
                </tr>
                <tr v-if="item.publisher" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.field_publisher') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ item.publisher }}</td>
                </tr>
                <tr v-if="item.published" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.field_published') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ item.published }}</td>
                </tr>
                <tr v-if="item.series" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.field_series') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ item.series }}</td>
                </tr>
                <tr v-if="item.tags" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.field_tags') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ item.tags }}</td>
                </tr>
                <tr v-if="item.languages" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.field_languages') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ item.languages }}</td>
                </tr>
                <tr v-if="item.identifiers" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.field_identifiers') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400 break-all">{{ item.identifiers }}</td>
                </tr>
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.size') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ formatBytes(item.size, 0) }} ({{ item.size }} {{ t('app.bytes') }})</td>
                </tr>
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">
                    {{ t(item.locations && item.locations.length > 1 ? 'app.locations' : 'app.location') }}
                  </th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">
                    <div
                      v-for="loc in (item.locations && item.locations.length ? item.locations : [currentPath])"
                      :key="loc"
                      class="break-all"
                      :class="{ 'font-semibold text-gray-900 dark:text-gray-100': loc === currentPath }"
                    >
                      <router-link
                        :to="`/browse/${loc.split('/').slice(0, -1).join('/')}`"
                        class="hover:text-blue-600 dark:hover:text-blue-400 hover:underline"
                      >/{{ loc.split('/').slice(0, -1).join('/') || 'Root' }}</router-link>/<span class="font-normal">{{ loc.split('/').slice(-1)[0] }}</span>
                    </div>
                  </td>
                </tr>
                <tr v-if="item.is_recommended && item.recommended_by_name" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.recommended_by_label') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ recByValue(item) }}</td>
                </tr>
                <tr v-if="currentUser?.is_admin && item.hash_id" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('admin.integrity.last_verified') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">
                    <template v-if="item.last_verified_at">
                      {{ formatDate(item.last_verified_at) }}
                      <span
                        class="ml-2 inline-flex items-center rounded px-2 py-0.5 text-xs font-medium"
                        :class="item.last_verified_ok
                          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                          : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'"
                      >
                        {{ item.last_verified_ok ? t('admin.integrity.ok') : t('admin.integrity.failed') }}
                        ({{ item.last_verified_mode }})
                      </span>
                      <span v-if="!item.last_verified_ok && item.last_verified_error" class="ml-2 text-xs text-red-500">
                        — {{ verifyErrorLabel(item.last_verified_error) }}
                      </span>
                    </template>
                    <template v-else>{{ t('admin.integrity.never') }}</template>
                  </td>
                </tr>
                <tr v-if="item.import_date" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-3 font-medium text-gray-900 dark:text-gray-100 align-top whitespace-nowrap">{{ t('app.imported') }}</th>
                  <td class="px-6 py-3 text-gray-600 dark:text-gray-400">{{ formatDate(item.import_date) }}</td>
                </tr>
              </tbody>
            </table>
          </details>

          <!-- Actions -->
          <div class="flex flex-wrap gap-4 pt-4">
            <!-- Signed-in: real download. Guest: nudge to the login/register page. -->
            <a v-if="currentUser" :href="getDownloadUrl()" download class="inline-flex items-center px-6 py-3 border border-transparent text-base font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors">
              <ArrowDownTrayIcon class="-ml-1 mr-2 h-5 w-5" aria-hidden="true" />
              {{ t('app.download') }} {{ formatBytes(item.size, 0) }}
            </a>
            <router-link v-else :to="{ name: 'login' }" class="inline-flex items-center px-6 py-3 border border-transparent text-base font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors">
              <ArrowDownTrayIcon class="-ml-1 mr-2 h-5 w-5" aria-hidden="true" />
              {{ t('app.download_signin') }}
            </router-link>
          </div>

          <!-- Ratings & Comments (collapsed by default) — hidden for guests -->
          <details v-if="item.hash_id && currentUser" class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm group">
            <summary class="px-6 py-4 cursor-pointer font-medium text-gray-900 dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-700/50 list-none flex items-center justify-between rounded-lg">
              <span>
                {{ t('app.comments') }}
                <span v-if="comments.length" class="text-gray-400 font-normal"> ({{ comments.length }})</span>
              </span>
              <span class="transition-transform group-open:rotate-90 text-gray-400">›</span>
            </summary>
            <div class="px-6 pb-6 space-y-6">

              <!-- Your rating + (when you have not commented yet) a new-comment form -->
              <div class="border-b border-gray-100 dark:border-gray-700 pb-5">
                <div class="flex items-center gap-3 flex-wrap">
                  <span class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('app.your_rating') }}</span>
                  <StarRating :rating="myRating" :interactive="true" :show-count="false" size="h-6 w-6" @update:rating="onRate" />
                </div>
                <div v-if="!myComment" class="mt-3">
                  <textarea v-model="reviewDraft" rows="3" :placeholder="t('app.write_review_placeholder')" class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm text-gray-900 dark:text-gray-100"></textarea>
                  <button @click="submitReview()" :disabled="reviewBusy || !reviewDraft.trim()" class="mt-2 px-4 py-1.5 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">{{ t('app.submit_review') }}</button>
                </div>
              </div>

              <!-- Comment list (your own comment is shown inline, with its replies) -->
              <p v-if="!comments.length" class="text-sm text-gray-500 dark:text-gray-400">{{ t('app.no_comments') }}</p>
              <div v-for="c in comments" :key="c.id" class="space-y-3">
                <div>
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ c.author_name }}</span>
                    <StarRating v-if="c.rating" :rating="c.rating" :show-count="false" size="h-4 w-4" />
                    <span class="text-xs text-gray-400">{{ formatDate(c.created_at) }}</span>
                    <span v-if="c.status === 'pending'" class="text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">{{ t('app.pending_moderation') }}</span>
                  </div>
                  <div v-if="editingCommentId === c.id" class="mt-1">
                    <textarea v-model="editDraft" rows="3" class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm text-gray-900 dark:text-gray-100"></textarea>
                    <div class="mt-2 flex gap-2">
                      <button @click="saveEdit()" :disabled="reviewBusy || !editDraft.trim()" class="px-3 py-1.5 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">{{ t('app.save') }}</button>
                      <button @click="editingCommentId = null" class="px-3 py-1.5 rounded-md text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">{{ t('app.cancel') }}</button>
                    </div>
                  </div>
                  <template v-else>
                    <p class="mt-1 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{{ c.body }}</p>
                    <div class="mt-1 flex items-center gap-3 text-xs">
                      <button v-if="c.is_own" @click="startEdit(c)" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.edit') }}</button>
                      <button v-else @click="startReply(c)" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.reply') }}</button>
                      <button v-if="c.is_own || currentUser?.is_admin" @click="removeComment(c)" class="text-red-600 dark:text-red-400 hover:underline">{{ t('app.delete') }}</button>
                    </div>
                  </template>
                  <div v-if="replyToId === c.id" class="mt-2">
                    <textarea v-model="replyDraft" rows="2" :placeholder="t('app.write_reply_placeholder')" class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm text-gray-900 dark:text-gray-100"></textarea>
                    <div class="mt-2 flex gap-2">
                      <button @click="submitReply()" :disabled="reviewBusy || !replyDraft.trim()" class="px-3 py-1.5 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">{{ t('app.reply') }}</button>
                      <button @click="replyToId = null" class="px-3 py-1.5 rounded-md text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">{{ t('app.cancel') }}</button>
                    </div>
                  </div>
                </div>
                <div v-for="r in c.replies" :key="r.id" class="ml-6 pl-4 border-l-2 border-gray-100 dark:border-gray-700">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ r.author_name }}</span>
                    <span class="text-xs text-gray-400">{{ formatDate(r.created_at) }}</span>
                    <span v-if="r.status === 'pending'" class="text-xs px-2 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">{{ t('app.pending_moderation') }}</span>
                  </div>
                  <div v-if="editingCommentId === r.id" class="mt-1">
                    <textarea v-model="editDraft" rows="2" class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm text-gray-900 dark:text-gray-100"></textarea>
                    <div class="mt-2 flex gap-2">
                      <button @click="saveEdit()" :disabled="reviewBusy || !editDraft.trim()" class="px-3 py-1.5 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">{{ t('app.save') }}</button>
                      <button @click="editingCommentId = null" class="px-3 py-1.5 rounded-md text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">{{ t('app.cancel') }}</button>
                    </div>
                  </div>
                  <template v-else>
                    <p class="mt-1 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{{ r.body }}</p>
                    <div v-if="r.is_own || currentUser?.is_admin" class="mt-1 flex items-center gap-3 text-xs">
                      <button v-if="r.is_own" @click="startEdit(r)" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.edit') }}</button>
                      <button @click="removeComment(r)" class="text-red-600 dark:text-red-400 hover:underline">{{ t('app.delete') }}</button>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </details>
        </div>
      </div>

      <!-- Built-in Viewer -->
      <div class="px-2 md:px-8 pt-8 w-full">
        <div class="rounded-xl overflow-hidden shadow-inner border border-gray-200 dark:border-gray-700 min-h-[500px] flex items-center justify-center bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
          <!-- Audio Player -->
          <audio v-if="isAudio" controls class="w-full max-w-md" :src="getDownloadUrl()">
            Your browser does not support the audio element.
          </audio>
          
          <!-- Video Player -->
          <video v-else-if="isVideo" controls class="w-full max-w-4xl" :src="getDownloadUrl()">
            Your browser does not support the video tag.
          </video>
          
          <!-- Image Viewer -->
          <ImageViewer v-else-if="isImage" :src="getDownloadUrl()" />
          
          <!-- PDF Viewer (pdfjs-dist) -->
          <PdfViewer v-else-if="isPdf" :source="viewerSource" />

          <!-- DjVu Viewer -->
          <DjvuViewer v-else-if="isDjvu" :source="viewerSource" />

          <!-- EPUB Viewer -->
          <EpubViewer v-else-if="isEpub" :source="viewerSource" />

          <!-- FB2 Viewer (also handles .fb2.zip) -->
          <Fb2Viewer v-else-if="isFb2" :source="viewerSource" />

          <!-- Markdown / plain text / code viewer -->
          <MdViewer v-else-if="isMd || isTxt || isCode" :source="viewerSource" />

          <!-- HTML Viewer (also handles .html.zip) -->
          <HtmlViewer v-else-if="isHtml" :source="viewerSource" />

          <!-- Unsupported -->
          <div v-else class="text-center p-8">
            <DocumentIcon class="mx-auto h-16 w-16 text-gray-400 mb-4" />
            <p class="text-lg">{{ t('app.preview_not_available') }}</p>
            <p class="text-sm mt-2 text-gray-500">{{ t('app.please_download') }}</p>
          </div>
          
        </div>
      </div>

    </div>

    <BookMetadataEditor :hash-id="editingId" @close="editingId = null" @saved="onEditorSaved" @deleted="onEditorDeleted" />

    <!-- Integrity verification modal -->
    <div
      v-if="verifyOpen"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      @click.self="closeVerify()"
    >
      <div class="w-full max-w-2xl bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 max-h-[90vh] flex flex-col">
        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h3 class="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <ShieldCheckIcon class="h-5 w-5 text-emerald-500" />
            {{ t('admin.integrity.title') }}
          </h3>
          <button @click="closeVerify()" class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700">
            <XMarkIcon class="h-5 w-5 text-gray-500" />
          </button>
        </div>
        <div class="px-6 py-4 overflow-y-auto space-y-4">
          <div class="flex flex-wrap gap-2">
            <button
              :disabled="verifying"
              @click="runVerify('quick')"
              class="px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-wait"
            >
              {{ t('admin.integrity.quick') }}
            </button>
            <button
              :disabled="verifying"
              @click="runVerify('full')"
              class="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-wait"
            >
              {{ t('admin.integrity.full') }}
            </button>
            <div v-if="verifying" class="ml-2 self-center text-sm text-gray-500 flex items-center gap-2">
              <div class="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-500"></div>
              {{ t('admin.integrity.running') }}
            </div>
          </div>

          <div v-if="verifyError" class="rounded-lg bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 px-4 py-3 text-sm">
            {{ verifyError }}
          </div>

          <div v-if="verifyResult" class="space-y-3">
            <div
              class="rounded-lg px-4 py-3 flex items-center gap-3"
              :class="verifyResult.ok
                ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200'
                : 'bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-200'"
            >
              <CheckCircleIcon v-if="verifyResult.ok" class="h-6 w-6 flex-shrink-0" />
              <XCircleIcon v-else class="h-6 w-6 flex-shrink-0" />
              <div>
                <div class="font-semibold">
                  {{ verifyResult.ok ? t('admin.integrity.ok') : t('admin.integrity.failed') }}
                  <span class="ml-1 text-xs opacity-75">({{ verifyResult.mode }})</span>
                </div>
                <div v-if="!verifyResult.ok && verifyResult.error" class="text-sm">
                  {{ verifyErrorLabel(verifyResult.error) }}
                </div>
                <div class="text-xs opacity-75 mt-0.5">
                  {{ t('admin.integrity.verified_at') }}: {{ formatDate(verifyResult.verified_at) }}
                </div>
              </div>
            </div>

            <div v-if="verifyResult.db_update_failed" class="rounded-lg bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-200 px-4 py-2 text-sm">
              {{ t('admin.integrity.db_update_failed') }}
            </div>

            <div class="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
              <table class="w-full text-sm">
                <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                  <tr v-for="c in verifyResult.checks" :key="c.name" class="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td class="px-4 py-2 w-8">
                      <CheckCircleIcon v-if="c.ok" class="h-5 w-5 text-emerald-500" />
                      <XCircleIcon v-else class="h-5 w-5 text-red-500" />
                    </td>
                    <td class="px-2 py-2 text-gray-900 dark:text-gray-100">{{ checkLabel(c.name) }}</td>
                    <td class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400 break-all">
                      <template v-if="Array.isArray(c.detail)">
                        <div v-for="(d, i) in c.detail" :key="i">{{ d.symlink_path }}: {{ d.reason }}</div>
                      </template>
                      <template v-else>{{ formatScalarDetail(c.name, c.detail) }}</template>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
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