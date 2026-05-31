<script setup lang="ts">
// /playlists/:id — owner view of one playlist. Header + actions, Grid/List
// toggle (shared 'viewMode' key), owner-only HTML5 drag-reorder persisted via
// PUT .../order, add/share/edit. The old /bookshelf view folds in here.
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowLeftIcon, PlusIcon, ShareIcon, PencilSquareIcon, GlobeAltIcon,
  Squares2X2Icon, ListBulletIcon, BookmarkIcon,
} from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'
import CoverCollage from '../components/CoverCollage.vue'
import VisibilityBadge from '../components/VisibilityBadge.vue'
import PlaylistBookCard from '../components/PlaylistBookCard.vue'
import PlaylistBookRow from '../components/PlaylistBookRow.vue'
import PlaylistEditDialog from '../components/PlaylistEditDialog.vue'
import ShareDialog from '../components/ShareDialog.vue'
import AddToPlaylistPopover from '../components/AddToPlaylistPopover.vue'
import { gridItemSize, GRID_CLASSES } from '../composables/useGridItemSize'
import { formatShortDate } from '../lib/itemFormat'
import {
  getPlaylist, reorderPlaylist,
  type PlaylistSummary, type PlaylistItem, type PlaylistVisibility,
} from '../api'
import { useEditClearance } from '../composables/useEditClearance'
import { playlistItemTarget } from '../composables/usePlaylistItem'

const props = defineProps<{ id: string }>()
const router = useRouter()
const { t, locale } = useI18n({ useScope: 'global' })
const { editClearance: onEditClearance } = useEditClearance()

const pid = computed(() => Number(props.id))
const playlist = ref<PlaylistSummary | null>(null)
const items = ref<PlaylistItem[]>([])
const loading = ref(true)
const error = ref('')

const savedViewMode = localStorage.getItem('viewMode')
const viewMode = ref<'grid' | 'list'>(savedViewMode === 'list' ? 'list' : 'grid')
watch(viewMode, (v) => localStorage.setItem('viewMode', v))

const showEdit = ref(false)
const showShare = ref(false)

const displayName = computed(() => {
  const p = playlist.value
  if (!p) return ''
  return p.is_bookshelf && p.name === 'Bookshelf' ? t('playlists.bookshelf_name') : p.name
})
const canReorder = computed(() => items.value.length >= 2)

// Monotonic token so a slow response for a previous playlist id can't clobber
// the current one. Fast back/forward between /playlists/5 and /6 starts two
// loads; only the newest is allowed to commit its result (and toggle loading).
let loadSeq = 0
const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  try {
    const res = await getPlaylist(pid.value)
    if (seq !== loadSeq) return // a newer load superseded this one
    playlist.value = res.data.playlist
    items.value = res.data.items
  } catch (e: any) {
    if (seq !== loadSeq) return
    error.value = e.response?.status === 403 || e.response?.status === 404
      ? 'Not found'
      : (e.response?.data?.detail || e.message)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(load)
watch(pid, load)

// --- add-to-playlist popover (replaces the old per-item remove button) ---
// The popover commits each tick/untick immediately server-side. We keep the
// row in place while it's open and reconcile on close: if *this* playlist was
// unticked (and not re-ticked), the item left it, so drop the row. Deferring to
// close keeps the popover's anchor stable and makes untick→re-tick a no-op.
type PopoverTarget = { book_hash_id?: string; dir_path?: string; title?: string }
const popoverTarget = ref<PopoverTarget | null>(null)
const popoverPos = ref<{ top: number; left: number }>({ top: 0, left: 0 })
const popoverItemId = ref<number | null>(null)
// Set to the open item's id once it's unticked from this playlist; cleared if
// re-ticked. Drives the "leaving" dim and the on-close removal.
const pendingRemovalId = ref<number | null>(null)

const openPlaylistPopover = (item: PlaylistItem, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  popoverPos.value = { top: rect.bottom + 4, left: rect.left }
  popoverItemId.value = item.id
  pendingRemovalId.value = null
  popoverTarget.value = playlistItemTarget(item)
}

const onMembershipChanged = (payload?: { playlistId: number; checked: boolean }) => {
  // Only this playlist's membership affects this view. checked=false → the open
  // item just left this playlist, so mark it to drop when the popover closes.
  if (!payload || payload.playlistId !== pid.value) return
  pendingRemovalId.value = payload.checked ? null : popoverItemId.value
}

const onPopoverClose = () => {
  const removeId = pendingRemovalId.value
  popoverTarget.value = null
  popoverItemId.value = null
  pendingRemovalId.value = null
  if (removeId == null) return
  // The popover already deleted it server-side — just reconcile the view.
  items.value = items.value.filter((it) => it.id !== removeId)
  if (playlist.value) playlist.value.item_count = items.value.length
}

// --- drag reorder (owner only) ---
// We mutate items.value live on dragenter for visual feedback, but only the
// `drop` event signals an *accepted* drop — Esc/release-outside fires
// `dragend` with no preceding `drop`. So commit on drop, and on dragend
// detect "no drop happened" and revert by reloading from the server.
const dragIndex = ref<number | null>(null)
let dropped = false
const onDragStart = (i: number, e: DragEvent) => {
  if (!canReorder.value) return
  dragIndex.value = i
  dropped = false
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', String(i)) // Firefox needs payload
  }
}
const onDragEnter = (i: number) => {
  if (dragIndex.value === null || dragIndex.value === i) return
  const arr = [...items.value]
  const [moved] = arr.splice(dragIndex.value, 1)
  arr.splice(i, 0, moved)
  items.value = arr
  dragIndex.value = i
}
const onDrop = async () => {
  if (dragIndex.value === null) return
  dropped = true
  try {
    await reorderPlaylist(pid.value, items.value.map((it) => it.id))
  } catch (e) {
    console.error('reorder failed', e)
    await load() // re-sync on error
  }
}
const onDragEnd = async () => {
  const wasDragging = dragIndex.value !== null
  dragIndex.value = null
  if (wasDragging && !dropped) {
    // Cancelled (Esc, drop outside any droppable). Snap back to server order.
    await load()
  }
  dropped = false
}

const onEdited = (p: PlaylistSummary) => {
  showEdit.value = false
  if (playlist.value) {
    playlist.value.name = p.name
    playlist.value.description = p.description
    playlist.value.visibility = p.visibility
    playlist.value.share_token = p.share_token
  }
}
const onDeleted = () => {
  showEdit.value = false
  router.push('/playlists')
}
const onShareUpdated = (payload: { visibility: PlaylistVisibility; share_token: string | null }) => {
  if (playlist.value) {
    playlist.value.visibility = payload.visibility
    playlist.value.share_token = payload.share_token
  }
}
</script>

<template>
  <div class="max-w-7xl mx-auto space-y-6">
    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400"></div>
    </div>

    <div v-else-if="error || !playlist" class="text-center py-20">
      <p class="text-lg text-gray-500 dark:text-gray-400">{{ error || 'Not found' }}</p>
      <router-link to="/playlists" class="inline-block mt-4 text-blue-600 dark:text-blue-400 hover:underline">{{ t('playlists.back') }}</router-link>
    </div>

    <template v-else>
      <!-- Header card -->
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700">
        <div class="p-6">
          <router-link to="/playlists" class="inline-flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 mb-4">
            <ArrowLeftIcon class="h-4 w-4" /> {{ t('playlists.back') }}
          </router-link>
          <div class="flex gap-5">
            <div class="w-28 h-28 flex-shrink-0 rounded-lg overflow-hidden border border-gray-100 dark:border-gray-700">
              <CoverCollage :items="playlist.collage" />
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <h1 class="text-2xl font-bold text-gray-900 dark:text-white">{{ displayName }}</h1>
                <VisibilityBadge :visibility="playlist.visibility" />
                <span v-if="playlist.is_bookshelf" class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200">
                  <BookmarkIconSolid class="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" /> {{ t('playlists.default_badge') }}
                </span>
              </div>
              <p v-if="playlist.description" class="text-gray-600 dark:text-gray-300 mt-1">{{ playlist.description }}</p>
              <div class="mt-2 text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2 flex-wrap">
                <span class="font-semibold text-gray-700 dark:text-gray-200">{{ t('playlists.items_count', { n: playlist.item_count }, playlist.item_count) }}</span>
                <span>·</span>
                <span>{{ t('playlists.created') }} {{ formatShortDate(locale, playlist.created_at) }}</span>
                <span>·</span>
                <span>{{ t('playlists.updated') }} {{ formatShortDate(locale, playlist.updated_at) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Action row -->
        <div class="px-6 py-3 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between gap-3 flex-wrap">
          <div class="flex items-center gap-2 flex-wrap">
            <router-link to="/browse" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700" :title="t('playlists.add_books_hint')">
              <PlusIcon class="h-4 w-4" /> {{ t('playlists.add_books') }}
            </router-link>
            <button @click="showShare = true" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700">
              <ShareIcon class="h-4 w-4" /> {{ t('playlists.share') }}
            </button>
            <button @click="showEdit = true" class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700">
              <PencilSquareIcon class="h-4 w-4" /> {{ t('playlists.edit') }}
            </button>
            <router-link
              v-if="playlist.visibility === 'public' && playlist.share_token"
              :to="`/p/${playlist.share_token}`"
              target="_blank"
              rel="noopener"
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
            >
              <GlobeAltIcon class="h-4 w-4" /> {{ t('playlists.open_shared') }}
            </router-link>
          </div>
          <div class="flex items-center gap-3">
            <span v-if="canReorder" class="hidden sm:inline text-xs text-gray-400 dark:text-gray-500">{{ t('playlists.reorder_hint') }}</span>
            <div class="flex bg-gray-100 dark:bg-gray-900 rounded-lg p-1 border border-transparent dark:border-gray-700">
              <button @click="viewMode = 'grid'" :class="['p-1.5 rounded-md transition-colors', viewMode === 'grid' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']" :title="t('app.grid_view')">
                <Squares2X2Icon class="h-5 w-5" />
              </button>
              <button @click="viewMode = 'list'" :class="['p-1.5 rounded-md transition-colors', viewMode === 'list' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']" :title="t('app.list_view')">
                <ListBulletIcon class="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Empty -->
      <div v-if="items.length === 0" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
        <BookmarkIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
        <p class="text-lg">{{ t('playlists.detail_empty') }}</p>
        <p class="text-sm mt-1">{{ t('playlists.detail_empty_hint') }}</p>
        <router-link to="/browse" class="inline-flex items-center gap-1.5 mt-4 px-4 py-2 rounded-md text-sm font-medium bg-blue-600 text-white hover:bg-blue-700">
          <PlusIcon class="h-4 w-4" /> {{ t('playlists.add_books') }}
        </router-link>
      </div>

      <!-- Grid -->
      <div v-else-if="viewMode === 'grid'" :class="['grid', GRID_CLASSES[gridItemSize]]">
        <div
          v-for="(item, i) in items"
          :key="item.id"
          :draggable="canReorder"
          @dragstart="onDragStart(i, $event)"
          @dragenter.prevent="onDragEnter(i)"
          @dragover.prevent
          @drop.prevent="onDrop"
          @dragend="onDragEnd"
          :class="[(dragIndex === i || item.id === pendingRemovalId) ? 'opacity-40' : '', item.id === pendingRemovalId ? 'pointer-events-none' : '', 'transition-opacity']"
        >
          <PlaylistBookCard :item="item" mode="owner" :draggable="canReorder" :from="`playlist:${pid}`" @open-menu="(ev) => openPlaylistPopover(item, ev)" @edit-clearance="onEditClearance" />
        </div>
      </div>

      <!-- List -->
      <div v-else class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
        <ul class="divide-y divide-gray-100 dark:divide-gray-700">
          <li
            v-for="(item, i) in items"
            :key="item.id"
            :draggable="canReorder"
            @dragstart="onDragStart(i, $event)"
            @dragenter.prevent="onDragEnter(i)"
            @dragover.prevent
            @drop.prevent="onDrop"
            @dragend="onDragEnd"
            class="p-4 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
            :class="[(dragIndex === i || item.id === pendingRemovalId) ? 'opacity-40' : '', item.id === pendingRemovalId ? 'pointer-events-none' : '']"
          >
            <PlaylistBookRow :item="item" mode="owner" :draggable="canReorder" :from="`playlist:${pid}`" @open-menu="(ev) => openPlaylistPopover(item, ev)" @edit-clearance="onEditClearance" />
          </li>
        </ul>
      </div>
    </template>

    <PlaylistEditDialog v-if="showEdit && playlist" :playlist="playlist" @close="showEdit = false" @saved="onEdited" @deleted="onDeleted" />
    <ShareDialog v-if="showShare && playlist" :playlist="playlist" :items="items" @close="showShare = false" @updated="onShareUpdated" />
    <AddToPlaylistPopover v-if="popoverTarget" :position="popoverPos" :target="popoverTarget" @close="onPopoverClose" @changed="onMembershipChanged" />
  </div>
</template>
