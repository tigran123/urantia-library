<script setup lang="ts">
// Album view container — rendered by BrowseView when viewMode === 'album'.
// Turns the directory's audio files into a playable track list, owns the
// session-only reorder + the recursive (subtree) expansion, and publishes the
// active queue to the usePlayer singleton that drives the now-playing bar.
//
// Recursive mode loads the whole audio subtree in one /api/album-subtree call,
// which applies the same per-directory clearance filtering server-side.
import { ref, computed, watch, onBeforeUnmount, inject, type Ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { MusicalNoteIcon } from '@heroicons/vue/24/outline'
import api from '../../api'
import AlbumHeader from './AlbumHeader.vue'
import TrackList from './TrackList.vue'
import AddToPlaylistPopover from '../AddToPlaylistPopover.vue'
import { usePlayer, type AlbumTrack } from '../../composables/usePlayer'
import { isAudioFile, fileTypeLabel } from '../../lib/itemFormat'
import { trackCover } from '../../lib/audioCover'

const props = defineProps<{ items: any[]; currentPath: string; favoriteIds?: Set<string> }>()
const emit = defineEmits<{ (e: 'membership-changed'): void }>()

const { t } = useI18n({ useScope: 'global' })
const router = useRouter()
const route = useRoute()
const currentUser = inject<Ref<{ email?: string } | null>>('currentUser', ref(null))
const player = usePlayer()

const dirName = computed(() => props.currentPath.split('/').filter(Boolean).pop() || t('album.root_name'))

function toTrack(item: any, groupPath?: string, groupName?: string): AlbumTrack {
  return {
    id: item.path,
    name: item.name,
    path: item.path,
    title: item.title || item.name,
    artist: item.author || '',
    cover_url: item.cover_url || null,
    size: item.size ?? null,
    hash_id: item.hash_id,
    rating: item.avg_rating ?? null,
    ratingCount: item.rating_count ?? 0,
    fmt: fileTypeLabel(item.name) || 'MP3',
    duration: item.duration ?? null,
    bitrate: item.bitrate ?? null,
    groupPath,
    groupName,
  }
}

// Flat (this-directory) tracks, in directory order.
const flatTracks = computed<AlbumTrack[]>(() =>
  props.items.filter((i: any) => !i.is_dir && isAudioFile(i.name)).map((i) => toTrack(i)),
)

// Session-only working order, persisted per directory in sessionStorage: a
// custom drag order survives navigating around and toggling Grid<->Album within
// the tab, but clears when the tab/browser closes (it never silently outlives
// the session). New files append in directory order; removed ones drop out.
const orderKey = computed(() => `albumOrder:${props.currentPath}`)

function applySavedOrder(tracks: AlbumTrack[]): AlbumTrack[] {
  let saved: string[] | null = null
  try {
    const raw = sessionStorage.getItem(orderKey.value)
    if (raw) saved = JSON.parse(raw)
  } catch { /* corrupt entry — fall through to directory order */ }
  if (!Array.isArray(saved)) return [...tracks]
  const byPath = new Map(tracks.map((t) => [t.path, t]))
  const ordered: AlbumTrack[] = []
  for (const p of saved) {
    const t = byPath.get(p)
    if (t) { ordered.push(t); byPath.delete(p) }
  }
  for (const t of tracks) if (byPath.has(t.path)) ordered.push(t) // new files, dir order
  return ordered
}

const orderedTracks = ref<AlbumTrack[]>([])
watch(flatTracks, (ts) => { orderedTracks.value = applySavedOrder(ts) }, { immediate: true })

// --- recursive (subtree) mode ---
const recursive = ref(false)
const groups = ref<{ path: string; name: string; tracks: AlbumTrack[] }[]>([])
const recLoading = ref(false)

// The whole bounded, clearance-filtered audio subtree comes from one server call
// (/api/album-subtree). A monotonic token drops a stale response that lands after
// the user navigated or toggled recursive off; an AbortController cancels the
// in-flight request on supersede/unmount so it never lingers.
let subtreeSeq = 0
let subtreeAbort: AbortController | null = null

async function loadSubtree() {
  const seq = ++subtreeSeq
  const rootPath = props.currentPath
  subtreeAbort?.abort()
  const ac = new AbortController()
  subtreeAbort = ac
  recLoading.value = true
  groups.value = []
  try {
    const res = await api.get('/album-subtree', { params: { path: rootPath }, signal: ac.signal })
    if (seq !== subtreeSeq) return // superseded / navigated away
    groups.value = (res.data.groups || []).map((g: any) => ({
      path: g.path,
      // Localize the root group's label (the server sends a plain basename).
      name: g.path === rootPath ? dirName.value : g.name,
      tracks: (g.tracks || []).map((it: any) => toTrack(it, g.path, g.name)),
    }))
  } catch {
    if (seq === subtreeSeq) groups.value = [] // error or aborted — show empty
  } finally {
    if (seq === subtreeSeq) recLoading.value = false
  }
}

// Unmount must cancel any in-flight subtree request and stop a late response from
// writing to the dead instance's refs.
onBeforeUnmount(() => { subtreeSeq++; subtreeAbort?.abort() })

// Decide "Include subdirectories" deterministically per directory: on for a
// non-root directory with no direct audio of its own (an artist directory of
// album subdirectories, so the groups render immediately); off at the library
// root and in directories with direct audio. Crucially this never carries a
// `recursive` of `true` into the root, which would walk the whole library and
// hammer the backend. The user can still toggle it on at the root explicitly
// (the server-side walk is bounded and abortable).
// Also watch the query, not just the path: the now-playing bar's "back to origin"
// link can target the directory we're already on (only ?recursive=1|0 changes, not
// currentPath), and a path-only watcher would never re-derive then. An explicit
// param always wins; without one we re-derive the per-directory default only when the
// directory itself changed, so an unrelated query change (the ?view=album strip, or
// "open current dir") doesn't clobber a manual header toggle.
watch([() => props.currentPath, () => route.query.recursive], ([path, q], oldVals) => {
  if (q === '1') { recursive.value = true; return }
  if (q === '0') { recursive.value = false; return }
  if (path !== oldVals?.[0]) recursive.value = !!path && !flatTracks.value.length
}, { immediate: true })

// (Re)load the subtree whenever recursive turns on or we navigate while it's on.
// Turning it off must cancel any in-flight request and drop the spinner at once.
watch([recursive, () => props.currentPath], () => {
  if (recursive.value) {
    loadSubtree()
  } else {
    subtreeSeq++
    subtreeAbort?.abort()
    recLoading.value = false
    groups.value = []
  }
}, { immediate: true })

// The playback queue: flat order, or the subtree flattened in tree order.
const queueTracks = computed<AlbumTrack[]>(() =>
  recursive.value ? groups.value.flatMap((g) => g.tracks) : orderedTracks.value,
)

// The navigable context for this view — the now-playing bar's "back to origin"
// link reopens it (directory + recursive state, in Album mode).
const viewContext = computed(() => ({
  path: props.currentPath,
  name: dirName.value,
  recursive: recursive.value,
}))

// Publish the queue + navigable context to the app-level player. This never
// interrupts a track already playing — only next/prev follow the new queue.
// Durations ride along on each track (from the backend), so there's nothing to probe.
watch([queueTracks, viewContext], () => {
  player.setQueue(queueTracks.value, viewContext.value)
}, { immediate: true })

const covers = computed(() => queueTracks.value.slice(0, 4).map((tr) => trackCover(tr)))
const sharedArtist = computed(() => {
  const arts = new Set(queueTracks.value.map((tr) => tr.artist).filter(Boolean))
  return arts.size === 1 ? [...arts][0] : ''
})

const onReorder = (tracks: AlbumTrack[]) => {
  orderedTracks.value = tracks
  try {
    sessionStorage.setItem(orderKey.value, JSON.stringify(tracks.map((t) => t.path)))
  } catch { /* storage full/disabled — order still applies for this mount */ }
}

// --- add-to-playlist popover (rows) ---
const popoverTarget = ref<{ book_hash_id?: string; title?: string } | null>(null)
const popoverPos = ref<{ top: number; left: number }>({ top: 0, left: 0 })

const onAddToPlaylist = (track: AlbumTrack, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!currentUser.value) { router.push({ name: 'login' }); return }
  if (!track.hash_id) return
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  popoverPos.value = { top: rect.bottom + 4, left: rect.left }
  popoverTarget.value = { book_hash_id: track.hash_id, title: track.title }
}
</script>

<template>
  <div class="space-y-6">
    <AlbumHeader
      :dir-name="dirName"
      :tracks="queueTracks"
      :covers="covers"
      :shared-artist="sharedArtist"
      :recursive="recursive"
      @update:recursive="recursive = $event"
    />

    <div v-if="recLoading" class="flex justify-center items-center py-16">
      <div class="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400"></div>
    </div>

    <!-- empty: nothing playable here (e.g. recursive toggled off in a directory
         with no direct audio, or the walk found nothing readable) -->
    <div
      v-else-if="queueTracks.length === 0"
      class="text-center py-16 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm"
    >
      <MusicalNoteIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
      <!-- Recursive off in a directory with no direct audio: we know the
           subtree has audio (the Album button only shows then), so point the
           user at the toggle rather than claiming there's nothing. -->
      <p class="text-lg">{{ recursive ? t('album.empty') : t('album.empty_flat') }}</p>
    </div>

    <TrackList
      v-else
      :tracks="orderedTracks"
      :groups="groups"
      :recursive="recursive"
      :favorite-ids="favoriteIds"
      @play="player.playFromView"
      @download="player.downloadTrack"
      @reorder="onReorder"
      @add-to-playlist="onAddToPlaylist"
    />

    <AddToPlaylistPopover
      v-if="popoverTarget"
      :position="popoverPos"
      :target="popoverTarget"
      @close="popoverTarget = null"
      @changed="emit('membership-changed')"
    />
  </div>
</template>
