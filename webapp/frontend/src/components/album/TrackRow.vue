<script setup lang="ts">
// One track row in the Album view. Two layouts share one clickable/draggable
// root: a stacked card on phones (< md) where the title gets a full line, and
// the aligned fixed-width columns on md+ (matching TrackList's ColumnHeader).
// The format badge sits on the cover (like the Grid view), the composer rides
// inline after the title in italics (marquee-scrolls when it overflows), and
// the bitrate comes from the backend (ffprobe at import) on track.bitrate.
import { computed } from 'vue'
import { ArrowDownTrayIcon, BookmarkIcon, NoSymbolIcon } from '@heroicons/vue/24/outline'
import { PlayIcon, StarIcon, BookmarkIcon as BookmarkSolidIcon } from '@heroicons/vue/24/solid'
import Equalizer from './Equalizer.vue'
import GripDotsIcon from './icons/GripDotsIcon.vue'
import MarqueeText from './MarqueeText.vue'
import { usePlayer, type AlbumTrack } from '../../composables/usePlayer'
import { formatBytes, formatClock } from '../../lib/itemFormat'
import { trackCover } from '../../lib/audioCover'

const props = defineProps<{
  track: AlbumTrack
  num: number
  isCurrent: boolean
  isPlaying: boolean
  reorderable: boolean
  isFavorite?: boolean
}>()

const emit = defineEmits<{
  (e: 'play'): void
  (e: 'download'): void
  (e: 'add-to-playlist', event: Event): void
}>()

const { trackDur, isExcluded, toggleExcluded } = usePlayer()
const cover = computed(() => trackCover(props.track))
const dur = computed(() => trackDur(props.track))
const excluded = computed(() => isExcluded(props.track.path))
// The raw filename when it differs from the displayed title (mirrors the List
// view); the real "из «film»" source isn't in the browse payload.
const sub = computed(() => (props.track.title && props.track.title !== props.track.name ? props.track.name : ''))
// Bitrate from the backend (ffprobe at import), in bits/sec. Blank when unknown.
const bitrate = computed(() => {
  const br = props.track.bitrate
  if (!br || br <= 0) return ''
  return `${Math.round(br / 1000)} kbps`
})

const titleClass = computed(() => (props.isCurrent ? 'text-blue-700 dark:text-blue-300' : 'text-gray-900 dark:text-gray-100'))
</script>

<template>
  <div
    @click="emit('play')"
    class="group px-3 rounded-lg transition-colors"
    :class="[
      reorderable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer',
      isCurrent ? 'bg-blue-50/70 dark:bg-blue-900/20' : 'hover:bg-gray-50 dark:hover:bg-gray-700/40',
      excluded ? 'opacity-60' : '',
    ]"
  >
    <!-- ===== mobile (< md): stacked — title / composer · filename / controls ===== -->
    <div class="flex md:hidden items-center gap-3 py-2">
      <span class="w-6 shrink-0 flex items-center justify-center text-sm tabular-nums text-gray-400 dark:text-gray-500">
        <Equalizer v-if="isCurrent" :playing="isPlaying" />
        <span v-else>{{ num }}</span>
      </span>
      <div class="relative w-14 h-14 shrink-0">
        <img :src="cover" alt="" class="w-14 h-14 rounded object-cover border border-gray-200 dark:border-gray-700" />
        <span class="absolute bottom-0.5 right-0.5 rounded px-1 text-[9px] leading-tight font-mono font-semibold bg-gray-800/80 text-white">{{ track.fmt }}</span>
      </div>
      <div class="flex-1 min-w-0">
        <MarqueeText :active="isCurrent" class="font-medium" :class="titleClass" :title="track.artist ? `${track.title} — ${track.artist}` : track.title">
          {{ track.title }}<span v-if="track.artist" class="italic font-normal text-gray-500 dark:text-gray-400"> — {{ track.artist }}</span>
        </MarqueeText>
        <div v-if="sub" class="truncate text-xs text-gray-400 dark:text-gray-500 mt-0.5">{{ sub }}</div>
        <div class="flex items-center gap-2 mt-1 text-xs text-gray-400 dark:text-gray-500">
          <!-- meta: clips from the right on very narrow screens -->
          <div class="flex items-center gap-2 flex-1 min-w-0 overflow-hidden">
            <span v-if="bitrate" class="tabular-nums whitespace-nowrap shrink-0">{{ bitrate }}</span>
            <span class="tabular-nums whitespace-nowrap shrink-0">{{ formatBytes(track.size) }}</span>
            <span v-if="track.rating" class="flex items-center gap-0.5 text-amber-400 whitespace-nowrap shrink-0">
              <StarIcon class="h-3 w-3" /><span class="tabular-nums">{{ track.rating.toFixed(1) }}</span>
            </span>
          </div>
          <!-- controls: always visible -->
          <div class="flex items-center gap-1 shrink-0">
            <button @click.stop="emit('download')" :title="$t('app.download')" class="p-1 rounded-full text-gray-400 hover:text-blue-500">
              <ArrowDownTrayIcon class="h-4 w-4" />
            </button>
            <button
              v-if="track.hash_id"
              @click.stop="emit('add-to-playlist', $event)"
              :title="$t('playlists.add_to')"
              class="p-1 rounded-full"
              :class="isFavorite ? 'text-blue-500' : 'text-gray-400 hover:text-blue-500'"
            >
              <BookmarkSolidIcon v-if="isFavorite" class="h-4 w-4" />
              <BookmarkIcon v-else class="h-4 w-4" />
            </button>
            <span class="tabular-nums text-gray-500 dark:text-gray-400 px-0.5">{{ dur ? formatClock(dur) : '–:–' }}</span>
            <button
              @click.stop="toggleExcluded(track)"
              :title="excluded ? $t('album.include_track') : $t('album.exclude_track')"
              class="p-1 rounded-full"
              :class="excluded ? 'text-red-500' : 'text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-300'"
            >
              <NoSymbolIcon class="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== desktop (md+): aligned columns ===== -->
    <div class="hidden md:flex items-center gap-3 py-2.5">
      <!-- drag grip (flat list only) -->
      <span class="w-4 shrink-0 flex items-center justify-center text-gray-300 dark:text-gray-500">
        <GripDotsIcon v-if="reorderable" class="h-4 w-4 opacity-0 group-hover:opacity-100 transition-opacity" />
      </span>
      <!-- number / equalizer / play-on-hover -->
      <span class="w-7 shrink-0 flex items-center justify-center text-sm tabular-nums text-gray-400 dark:text-gray-500">
        <Equalizer v-if="isCurrent" :playing="isPlaying" />
        <template v-else>
          <span class="group-hover:hidden">{{ num }}</span>
          <PlayIcon class="h-4 w-4 hidden group-hover:block text-gray-700 dark:text-gray-200" />
        </template>
      </span>
      <!-- cover thumb with format badge -->
      <div class="relative w-10 h-10 shrink-0">
        <img :src="cover" alt="" class="w-10 h-10 rounded object-cover border border-gray-200 dark:border-gray-700" />
        <span class="absolute bottom-0 right-0 rounded px-0.5 text-[8px] leading-tight font-mono font-semibold bg-gray-800/80 text-white">{{ track.fmt }}</span>
      </div>
      <!-- title — inline italic composer; marquee-scrolls when it overflows -->
      <div class="flex-1 min-w-0">
        <MarqueeText :active="isCurrent" class="font-medium" :class="titleClass" :title="track.artist ? `${track.title} — ${track.artist}` : track.title">
          {{ track.title }}<span v-if="track.artist" class="italic font-normal text-gray-500 dark:text-gray-400"> — {{ track.artist }}</span>
        </MarqueeText>
        <div v-if="sub" class="truncate text-xs text-gray-400 dark:text-gray-500 mt-0.5">{{ sub }}</div>
      </div>
      <!-- rating (only when present) -->
      <span v-if="track.rating" class="flex w-16 justify-end items-center gap-1 shrink-0 text-amber-400" :title="`${track.rating.toFixed(1)} (${track.ratingCount})`">
        <StarIcon class="h-3.5 w-3.5" />
        <span class="text-xs font-medium text-gray-500 dark:text-gray-400 tabular-nums">{{ track.rating.toFixed(1) }}</span>
      </span>
      <span v-else class="w-16 shrink-0" />
      <!-- bitrate -->
      <span class="w-20 text-right shrink-0 text-xs text-gray-400 dark:text-gray-500 tabular-nums">{{ bitrate }}</span>
      <!-- size -->
      <span class="hidden lg:block w-16 text-right shrink-0 text-xs text-gray-400 dark:text-gray-500 tabular-nums">{{ formatBytes(track.size) }}</span>
      <!-- actions: download + add to playlist -->
      <span class="w-[68px] flex items-center justify-end gap-0.5 shrink-0">
        <button @click.stop="emit('download')" :title="$t('app.download')" class="p-1.5 rounded-full text-gray-400 hover:text-blue-500 hover:bg-white dark:hover:bg-gray-700">
          <ArrowDownTrayIcon class="h-4 w-4" />
        </button>
        <button
          v-if="track.hash_id"
          @click.stop="emit('add-to-playlist', $event)"
          :title="$t('playlists.add_to')"
          class="p-1.5 rounded-full hover:bg-white dark:hover:bg-gray-700"
          :class="isFavorite ? 'text-blue-500' : 'text-gray-400 hover:text-blue-500'"
        >
          <BookmarkSolidIcon v-if="isFavorite" class="h-4 w-4" />
          <BookmarkIcon v-else class="h-4 w-4" />
        </button>
      </span>
      <!-- duration -->
      <span class="w-12 text-right shrink-0 text-sm text-gray-400 dark:text-gray-500 tabular-nums">{{ dur ? formatClock(dur) : '–:–' }}</span>
      <!-- exclude from sequential/random playback -->
      <span class="w-7 shrink-0 flex items-center justify-center">
        <button
          @click.stop="toggleExcluded(track)"
          :title="excluded ? $t('album.include_track') : $t('album.exclude_track')"
          class="p-1 rounded-full transition-colors"
          :class="excluded ? 'text-red-500 hover:text-red-600' : 'text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-300'"
        >
          <NoSymbolIcon class="h-4 w-4" />
        </button>
      </span>
    </div>
  </div>
</template>
