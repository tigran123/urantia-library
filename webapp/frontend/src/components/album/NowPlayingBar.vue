<script setup lang="ts">
// Persistent "now playing" bar — fixed to the viewport bottom, mounted above the
// router view in App.vue so it survives route changes. Track identity (left),
// transport + seek scrubber (center), context + download + volume (right). All
// state lives in the usePlayer singleton; this component is pure presentation.
import { ref, inject, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowPathIcon, ArrowDownTrayIcon, SpeakerWaveIcon,
  MusicalNoteIcon, BookmarkIcon, XMarkIcon,
} from '@heroicons/vue/24/outline'
import { PlayIcon, PauseIcon, BackwardIcon, ForwardIcon } from '@heroicons/vue/24/solid'
import ShuffleIcon from './icons/ShuffleIcon.vue'
import Scrubber from './Scrubber.vue'
import AddToPlaylistPopover from '../AddToPlaylistPopover.vue'
import { usePlayer } from '../../composables/usePlayer'
import { trackCover } from '../../lib/audioCover'
import { formatClock } from '../../lib/itemFormat'

const { t } = useI18n({ useScope: 'global' })
const router = useRouter()
const currentUser = inject<Ref<{ email?: string } | null>>('currentUser', ref(null))

const {
  currentTrack, isPlaying, elapsed, duration, shuffle, repeat, volume, playbackOrigin,
  togglePlay, next, prev, seek, setVolume, toggleShuffle, cycleRepeat, downloadTrack, stop,
} = usePlayer()

// Shared transport-toggle styles, matching the album header (just smaller):
// ghost circle when off, solid blue fill when on.
const activeBtn = 'bg-blue-600 text-white hover:bg-blue-700 shadow-sm'
const ghostBtn = 'border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'

const popoverTarget = ref<{ book_hash_id?: string; title?: string } | null>(null)
const popoverPos = ref<{ top: number; left: number }>({ top: 0, left: 0 })

// Mobile volume: the inline slider is hidden on phones, so a speaker button
// opens a small popover instead.
const showVolume = ref(false)

// Left link: jump to the directory the playing track physically lives in, in
// Album view. The `?view=album` query makes BrowseView switch to Album mode
// even when it wasn't remounted.
const openCurrentDir = () => {
  const track = currentTrack.value
  if (!track) return
  const dir = track.path.split('/').slice(0, -1).join('/')
  router.push({ path: '/browse' + (dir ? '/' + dir : ''), query: { view: 'album' } })
}

// Right link: return to the Album view playback started from — its directory
// and "Include subdirectories" state — which may differ from the track's own
// directory (e.g. started a recursive album of a parent directory).
const openOrigin = () => {
  const o = playbackOrigin.value
  if (!o) return
  router.push({
    path: '/browse' + (o.path ? '/' + o.path : ''),
    query: { view: 'album', recursive: o.recursive ? '1' : '0' },
  })
}

const openPlaylistPopover = (event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  const track = currentTrack.value
  if (!track) return
  if (!currentUser.value) { router.push({ name: 'login' }); return }
  if (!track.hash_id) return
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  // The popover clamps itself within the viewport; since the bar sits at the
  // bottom it ends up just above the button.
  popoverPos.value = { top: rect.top, left: rect.left }
  popoverTarget.value = { book_hash_id: track.hash_id, title: track.title }
}
</script>

<template>
  <div
    v-if="currentTrack"
    class="fixed bottom-0 inset-x-0 z-40 bg-white/95 dark:bg-gray-800/95 backdrop-blur border-t border-gray-200 dark:border-gray-700"
    style="box-shadow: 0 -4px 20px rgba(0,0,0,0.06)"
  >
    <div class="px-3 sm:px-5 h-[72px] flex items-center gap-3 sm:gap-5">
      <!-- left: track identity (mobile: just the cover, natural width) -->
      <div class="flex items-center gap-3 min-w-0 sm:w-[26%]">
        <button
          @click="openCurrentDir"
          :title="t('album.open_directory')"
          class="group/nav flex items-center gap-3 min-w-0 text-left"
        >
          <img :src="trackCover(currentTrack)" alt="" class="w-12 h-12 rounded object-cover border border-gray-200 dark:border-gray-700 shrink-0" />
          <div class="min-w-0 hidden sm:block">
            <div class="truncate text-sm font-medium text-gray-900 dark:text-gray-100 group-hover/nav:underline" :title="currentTrack.title">{{ currentTrack.title }}</div>
            <div class="truncate text-xs text-gray-500 dark:text-gray-400">{{ currentTrack.artist }}</div>
          </div>
        </button>
        <!-- download + add-to-playlist, grouped (download on the left) to mirror the track rows -->
        <button
          @click="downloadTrack(currentTrack)"
          :title="t('app.download')"
          class="hidden sm:inline-flex p-1.5 rounded-full text-gray-400 hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-gray-700 shrink-0"
        >
          <ArrowDownTrayIcon class="h-4 w-4" />
        </button>
        <button
          v-if="currentTrack.hash_id"
          @click="openPlaylistPopover"
          :title="t('playlists.add_to')"
          class="hidden sm:inline-flex p-1.5 rounded-full text-gray-400 hover:text-blue-500 hover:bg-gray-100 dark:hover:bg-gray-700 shrink-0"
        >
          <BookmarkIcon class="h-4 w-4" />
        </button>
      </div>

      <!-- mobile volume: speaker button in the gap between the cover and the
           transport (the inline slider further right is desktop-only) -->
      <button
        @click.stop="showVolume = !showVolume"
        :title="t('album.volume')"
        class="md:hidden shrink-0 inline-flex items-center justify-center h-9 w-9 rounded-full transition-colors"
        :class="showVolume ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700'"
      >
        <SpeakerWaveIcon class="h-5 w-5" />
      </button>

      <!-- center: transport + scrubber -->
      <div class="flex-1 flex flex-col items-center justify-center gap-1 min-w-0">
        <div class="flex items-center gap-1 sm:gap-2">
          <button
            @click="toggleShuffle"
            :title="t('album.shuffle')"
            class="inline-flex items-center justify-center h-9 w-9 rounded-full transition-colors"
            :class="shuffle ? activeBtn : ghostBtn"
          >
            <ShuffleIcon class="h-4 w-4" />
          </button>
          <button @click="prev" :title="t('album.previous')" class="inline-flex items-center justify-center h-9 w-9 rounded-full text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors">
            <BackwardIcon class="h-5 w-5" />
          </button>
          <button
            @click="togglePlay"
            :title="isPlaying ? t('album.pause') : t('album.play')"
            class="inline-flex items-center justify-center h-11 w-11 rounded-full bg-blue-600 text-white hover:bg-blue-700 shadow-sm transition-colors"
          >
            <PauseIcon v-if="isPlaying" class="h-6 w-6" />
            <PlayIcon v-else class="h-6 w-6 ml-0.5" />
          </button>
          <button @click="next" :title="t('album.next')" class="inline-flex items-center justify-center h-9 w-9 rounded-full text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors">
            <ForwardIcon class="h-5 w-5" />
          </button>
          <button
            @click="cycleRepeat"
            :title="t('album.repeat')"
            class="relative inline-flex items-center justify-center h-9 w-9 rounded-full transition-colors"
            :class="repeat !== 'off' ? activeBtn : ghostBtn"
          >
            <ArrowPathIcon class="h-4 w-4" />
            <span v-if="repeat === 'one'" class="absolute -bottom-0.5 -right-0.5 h-3.5 w-3.5 rounded-full bg-white text-blue-600 text-[8px] font-bold flex items-center justify-center shadow-sm">1</span>
          </button>
        </div>
        <div class="w-full max-w-xl flex items-center gap-2">
          <span class="w-9 text-right text-[11px] tabular-nums text-gray-400 dark:text-gray-500">{{ formatClock(elapsed) }}</span>
          <Scrubber :value="elapsed" :max="duration" @seek="seek" class="flex-1" />
          <span class="w-9 text-[11px] tabular-nums text-gray-400 dark:text-gray-500">{{ formatClock(duration) }}</span>
        </div>
      </div>

      <!-- right: "back to where playback started" link + volume -->
      <div class="hidden md:flex items-center gap-2 justify-end">
        <button
          v-if="playbackOrigin"
          @click="openOrigin"
          :title="t('album.open_origin', { dir: playbackOrigin.name })"
          class="hidden lg:flex items-center gap-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline truncate mr-1"
        >
          <MusicalNoteIcon class="h-4 w-4 shrink-0" />
          <span class="truncate">{{ playbackOrigin.name }}</span>
        </button>
        <div class="flex items-center gap-1.5 w-28">
          <SpeakerWaveIcon class="h-5 w-5 text-gray-400 dark:text-gray-500 shrink-0" />
          <Scrubber :value="volume" :max="100" @seek="setVolume" class="flex-1" />
        </div>
      </div>

      <!-- close: stop playback and dismiss the bar (always visible, incl. mobile) -->
      <button
        @click="stop"
        :title="t('album.close')"
        class="shrink-0 inline-flex items-center justify-center h-9 w-9 rounded-full text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
      >
        <XMarkIcon class="h-5 w-5" />
      </button>
    </div>

    <!-- Mobile volume popover (Teleported out of the backdrop-blur bar so the
         fixed positioning resolves against the viewport). -->
    <Teleport to="body">
      <div v-if="showVolume" class="fixed inset-0 z-40 md:hidden" @click="showVolume = false"></div>
      <div
        v-if="showVolume"
        class="fixed bottom-[84px] left-3 z-50 md:hidden flex items-center gap-2 w-48 px-3 py-2 rounded-full bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-lg"
      >
        <SpeakerWaveIcon class="h-5 w-5 text-gray-400 dark:text-gray-500 shrink-0" />
        <Scrubber :value="volume" :max="100" @seek="setVolume" class="flex-1" />
        <span class="w-7 text-right text-[11px] tabular-nums text-gray-400 dark:text-gray-500">{{ Math.round(volume) }}</span>
      </div>
    </Teleport>

    <!-- Teleported to <body>: the bar's backdrop-blur makes it the containing
         block for fixed-position descendants, which would otherwise resolve the
         popover's coordinates against the 72px bar instead of the viewport. -->
    <Teleport to="body">
      <AddToPlaylistPopover
        v-if="popoverTarget"
        :position="popoverPos"
        :target="popoverTarget"
        @close="popoverTarget = null"
        @changed="() => {}"
      /><!-- no-op: unlike AlbumView, the bar renders no bookmark state to refresh -->
    </Teleport>
  </div>
</template>
