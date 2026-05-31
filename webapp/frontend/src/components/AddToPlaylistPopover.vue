<script setup lang="ts">
// Anchored popover listing the user's playlists as checkboxes (checked = the
// target is already in that playlist). Toggling adds/removes the book or
// directory. "+ New playlist…" opens the create dialog and drops the target
// into the new list on save. Emits `changed` whenever membership changes so the
// caller can refresh its filled-bookmark state.
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { PlusIcon, LockClosedIcon, GlobeAltIcon } from '@heroicons/vue/24/outline'
import {
  listPlaylists, getPlaylistMembership, addPlaylistItem, removePlaylistItemByTarget,
  type PlaylistSummary,
} from '../api'
import PlaylistEditDialog from './PlaylistEditDialog.vue'

const props = defineProps<{
  position: { top: number; left: number }
  target: { book_hash_id?: string; dir_path?: string; title?: string }
}>()
// `changed` carries which playlist toggled and its new state so a caller can
// react to a *specific* list (e.g. the playlist detail view drops the row when
// the current list is unticked). Callers that only refresh a filled-bookmark
// Set ignore the payload.
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'changed', payload?: { playlistId: number; checked: boolean }): void
}>()

const { t } = useI18n({ useScope: 'global' })

const playlists = ref<PlaylistSummary[]>([])
const checked = ref<Set<number>>(new Set())
const loading = ref(true)
const busy = ref<Set<number>>(new Set())
const showCreate = ref(false)
const root = ref<HTMLElement | null>(null)

const targetParams = computed(() =>
  props.target.book_hash_id ? { book_hash_id: props.target.book_hash_id } : { dir_path: props.target.dir_path },
)

// Clamp within the viewport so the popover never overflows an edge. The
// anchor point (position.top) sits just below the trigger; if the popover
// would spill past the bottom, shift it up so the whole thing — including the
// "+ New playlist" footer — stays reachable. `measuredHeight` is filled in
// after render (and after the async playlist list loads, which changes the
// height); until then we use a conservative estimate.
const measuredHeight = ref(0)
const style = computed(() => {
  const width = 256
  const height = measuredHeight.value || 320
  const left = Math.max(8, Math.min(props.position.left, window.innerWidth - width - 8))
  const top = Math.max(8, Math.min(props.position.top, window.innerHeight - height - 8))
  return { top: `${top}px`, left: `${left}px`, width: `${width}px` }
})
const remeasure = () => { measuredHeight.value = root.value?.offsetHeight ?? 0 }
// Re-measure once the spinner is replaced by the loaded list (height jumps).
watch([loading, playlists], () => nextTick(remeasure))

const displayName = (p: PlaylistSummary) =>
  p.is_bookshelf && p.name === 'Bookshelf' ? t('playlists.bookshelf_name') : p.name

const load = async () => {
  loading.value = true
  try {
    const [pls, mem] = await Promise.all([
      listPlaylists(),
      getPlaylistMembership(targetParams.value),
    ])
    playlists.value = pls.data.items
    checked.value = new Set(mem.data.playlist_ids)
  } finally {
    loading.value = false
  }
}

const toggle = async (p: PlaylistSummary) => {
  if (busy.value.has(p.id)) return
  busy.value = new Set(busy.value).add(p.id)
  const wasChecked = checked.value.has(p.id)
  try {
    if (wasChecked) {
      await removePlaylistItemByTarget(p.id, targetParams.value)
      checked.value.delete(p.id)
    } else {
      await addPlaylistItem(p.id, targetParams.value)
      checked.value.add(p.id)
    }
    checked.value = new Set(checked.value)
    emit('changed', { playlistId: p.id, checked: !wasChecked })
  } catch (e) {
    console.error('toggle playlist membership failed', e)
  } finally {
    const b = new Set(busy.value); b.delete(p.id); busy.value = b
  }
}

const onCreated = async (p: PlaylistSummary) => {
  showCreate.value = false
  try {
    await addPlaylistItem(p.id, targetParams.value)
    emit('changed')
  } catch (e) {
    console.error('add to new playlist failed', e)
  }
  await load()
}

const onDocClick = (e: MouseEvent) => {
  if (showCreate.value) return
  if (root.value && !root.value.contains(e.target as Node)) emit('close')
}
const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !showCreate.value) emit('close') }

onMounted(() => {
  load()
  nextTick(remeasure)
  // Defer so the click that opened the popover doesn't immediately close it.
  setTimeout(() => document.addEventListener('mousedown', onDocClick), 0)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div
    ref="root"
    class="fixed z-50 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800"
    :style="style"
    @mousedown.stop
  >
    <div class="px-3 pt-3 pb-2 border-b border-gray-100 dark:border-gray-700">
      <p class="text-[11px] font-semibold tracking-wide text-gray-400 dark:text-gray-500 uppercase">{{ t('playlists.add_to') }}</p>
      <p v-if="target.title" class="text-sm text-gray-700 dark:text-gray-200 truncate" :title="target.title">{{ target.title }}</p>
    </div>

    <div v-if="loading" class="px-3 py-4 text-center">
      <div class="inline-block animate-spin rounded-full h-5 w-5 border-b-2 border-blue-600"></div>
    </div>

    <ul v-else class="max-h-64 overflow-y-auto py-1">
      <li v-for="p in playlists" :key="p.id">
        <button
          @click="toggle(p)"
          :disabled="busy.has(p.id)"
          class="w-full flex items-center gap-2 px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700/60 disabled:opacity-60"
        >
          <input type="checkbox" :checked="checked.has(p.id)" tabindex="-1" class="pointer-events-none h-4 w-4 rounded text-blue-600 focus:ring-blue-500" />
          <span class="flex-1 truncate text-gray-800 dark:text-gray-100">{{ displayName(p) }}</span>
          <GlobeAltIcon v-if="p.visibility === 'public'" class="h-4 w-4 text-blue-500 flex-shrink-0" />
          <LockClosedIcon v-else class="h-4 w-4 text-gray-400 flex-shrink-0" />
        </button>
      </li>
    </ul>

    <div class="border-t border-gray-100 dark:border-gray-700">
      <button
        @click="showCreate = true"
        class="w-full flex items-center gap-2 px-3 py-2.5 text-sm font-medium text-blue-600 dark:text-blue-400 hover:bg-gray-50 dark:hover:bg-gray-700/60"
      >
        <PlusIcon class="h-4 w-4" /> {{ t('playlists.new_from_here') }}
      </button>
    </div>
  </div>

  <PlaylistEditDialog
    v-if="showCreate"
    :playlist="null"
    @close="showCreate = false"
    @saved="onCreated"
  />
</template>
