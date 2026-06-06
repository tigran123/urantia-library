<script setup lang="ts">
// The track-list card: a column header, then either a flat numbered list
// (drag-reorderable, session-only) or per-subdirectory groups (recursive mode,
// order follows the directory tree — not draggable). Drag-reorder mirrors
// PlaylistDetailView's pattern, but commits to a parent ref instead of the
// server: live-reorder on dragenter, emit on drop, revert on a cancelled drag.
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { FolderIcon } from '@heroicons/vue/24/outline'
import TrackRow from './TrackRow.vue'
import GripDotsIcon from './icons/GripDotsIcon.vue'
import { usePlayer, type AlbumTrack } from '../../composables/usePlayer'
import { formatRuntime } from '../../lib/itemFormat'

interface TrackGroup { path: string; name: string; tracks: AlbumTrack[] }

const props = defineProps<{
  tracks: AlbumTrack[]
  groups: TrackGroup[]
  recursive: boolean
  favoriteIds?: Set<string>
}>()

const emit = defineEmits<{
  (e: 'play', track: AlbumTrack): void
  (e: 'download', track: AlbumTrack): void
  (e: 'reorder', tracks: AlbumTrack[]): void
  (e: 'add-to-playlist', track: AlbumTrack, event: Event): void
}>()

const { t } = useI18n({ useScope: 'global' })
const { currentTrack, isPlaying, trackDur } = usePlayer()

const isCurrent = (track: AlbumTrack) => currentTrack.value?.id === track.id
const isFav = (track: AlbumTrack) => !!props.favoriteIds && !!track.hash_id && props.favoriteIds.has(track.hash_id)
const groupDur = (g: TrackGroup) => formatRuntime(g.tracks.reduce((s, x) => s + (trackDur(x) || 0), 0), t)

// Local copy for live drag feedback; resynced whenever the parent's order
// changes (commit or revert both flow back through props.tracks).
const rows = ref<AlbumTrack[]>([])
watch(() => props.tracks, (ts) => { rows.value = [...ts] }, { immediate: true })

const canReorder = computed(() => !props.recursive && rows.value.length >= 2)

const dragIndex = ref<number | null>(null)
let dropped = false

const onDragStart = (i: number, e: DragEvent) => {
  if (!canReorder.value) return
  dragIndex.value = i
  dropped = false
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', String(i)) // Firefox needs a payload
  }
}
const onDragEnter = (i: number) => {
  if (dragIndex.value === null || dragIndex.value === i) return
  const arr = [...rows.value]
  const [moved] = arr.splice(dragIndex.value, 1)
  arr.splice(i, 0, moved)
  rows.value = arr
  dragIndex.value = i
}
const onDrop = () => {
  if (dragIndex.value === null) return
  dropped = true
  emit('reorder', rows.value)
}
const onDragEnd = () => {
  const wasDragging = dragIndex.value !== null
  dragIndex.value = null
  if (wasDragging && !dropped) rows.value = [...props.tracks] // cancelled — revert
  dropped = false
}
</script>

<template>
  <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
    <div class="px-2 sm:px-3 pb-3">
      <!-- column header -->
      <div class="hidden md:flex items-center gap-3 px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700">
        <span class="w-4 shrink-0" />
        <span class="w-7 text-center shrink-0">#</span>
        <span class="w-10 shrink-0" />
        <span class="flex-1 min-w-0">{{ t('album.col_title') }}</span>
        <span class="w-16 shrink-0" />
        <span class="w-20 text-right shrink-0">{{ t('album.col_bitrate') }}</span>
        <span class="hidden lg:block w-16 text-right shrink-0">{{ t('album.col_size') }}</span>
        <span class="w-[68px] shrink-0" />
        <span class="w-12 text-right shrink-0">{{ t('album.col_time') }}</span>
        <span class="w-7 shrink-0" />
      </div>

      <!-- reorder hint (flat list only) -->
      <div v-if="canReorder" class="flex items-center gap-1.5 px-3 pt-2 text-[11px] text-gray-400 dark:text-gray-500 whitespace-nowrap">
        <GripDotsIcon class="h-3.5 w-3.5 shrink-0" />
        <span>{{ t('album.drag_hint') }}</span>
      </div>

      <!-- flat list -->
      <div v-if="!recursive" class="pt-1.5">
        <div
          v-for="(track, i) in rows"
          :key="track.id"
          :draggable="canReorder"
          @dragstart="onDragStart(i, $event)"
          @dragenter.prevent="onDragEnter(i)"
          @dragover.prevent
          @drop.prevent="onDrop"
          @dragend="onDragEnd"
          :class="dragIndex === i ? 'opacity-40 transition-opacity' : 'transition-opacity'"
        >
          <TrackRow
            :track="track"
            :num="i + 1"
            :is-current="isCurrent(track)"
            :is-playing="isPlaying"
            :reorderable="canReorder"
            :is-favorite="isFav(track)"
            @play="emit('play', track)"
            @download="emit('download', track)"
            @add-to-playlist="(e) => emit('add-to-playlist', track, e)"
          />
        </div>
      </div>

      <!-- grouped (recursive) list -->
      <template v-else>
        <div v-for="g in groups" :key="g.path">
          <div class="flex items-center gap-2.5 px-3 pt-5 pb-2">
            <span class="flex items-center justify-center h-7 w-7 rounded-md bg-blue-50 dark:bg-gray-700/60 text-blue-500 dark:text-blue-400 shrink-0">
              <FolderIcon class="h-4 w-4" />
            </span>
            <h3 class="font-semibold text-gray-800 dark:text-gray-100 truncate">{{ g.name }}</h3>
            <span class="text-xs text-gray-400 dark:text-gray-500 tabular-nums whitespace-nowrap">
              {{ t('album.group_tracks', { n: g.tracks.length }, g.tracks.length) }} · {{ groupDur(g) }}
            </span>
            <span class="flex-1 border-t border-gray-100 dark:border-gray-700 ml-1" />
          </div>
          <TrackRow
            v-for="(track, i) in g.tracks"
            :key="track.id"
            :track="track"
            :num="i + 1"
            :is-current="isCurrent(track)"
            :is-playing="isPlaying"
            :reorderable="false"
            :is-favorite="isFav(track)"
            @play="emit('play', track)"
            @download="emit('download', track)"
            @add-to-playlist="(e) => emit('add-to-playlist', track, e)"
          />
        </div>
      </template>
    </div>
  </div>
</template>
