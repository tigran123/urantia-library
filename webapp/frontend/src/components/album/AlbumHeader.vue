<script setup lang="ts">
// Album header: 2x2 cover collage, eyebrow + directory title + composer, the
// "<n> tracks · <runtime> · MP3" meta line, the Play all / Shuffle / Repeat
// transport, and the bottom "Include subdirectories" toggle strip. Transport is
// wired straight to the usePlayer singleton.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowPathIcon, MusicalNoteIcon } from '@heroicons/vue/24/outline'
import { PlayIcon } from '@heroicons/vue/24/solid'
import ShuffleIcon from './icons/ShuffleIcon.vue'
import { usePlayer, type AlbumTrack } from '../../composables/usePlayer'
import { formatRuntime } from '../../lib/itemFormat'

// Shared transport-button styles (active = solid blue fill, inactive = ghost).
const activeBtn = 'bg-blue-600 text-white hover:bg-blue-700 shadow-sm'
const ghostBtn = 'border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'

const props = defineProps<{
  dirName: string
  tracks: AlbumTrack[]
  covers: string[]
  sharedArtist: string
  recursive: boolean
}>()

const emit = defineEmits<{ (e: 'update:recursive', value: boolean): void }>()

const { t } = useI18n({ useScope: 'global' })
const { shuffle, repeat, trackDur, playAll, shuffleAll, cycleRepeat } = usePlayer()

// Pad to four tiles by repeating the last cover, like the prototype.
const four = computed(() => {
  const c = [...props.covers]
  while (c.length && c.length < 4) c.push(c[c.length - 1])
  return c.slice(0, 4)
})

const totalDur = computed(() => props.tracks.reduce((s, x) => s + (trackDur(x) || 0), 0))
const runtimeLabel = computed(() => (totalDur.value > 0 ? formatRuntime(totalDur.value, t) : '—'))

// All distinct formats present in the track set, most common first (name as
// tiebreak) — a mixed directory shows e.g. "MP3, FLAC".
const formats = computed(() => {
  const counts = new Map<string, number>()
  for (const x of props.tracks) {
    if (x.fmt) counts.set(x.fmt, (counts.get(x.fmt) || 0) + 1)
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([f]) => f)
    .join(', ')
})
</script>

<template>
  <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700">
    <div class="p-6 flex flex-col sm:flex-row gap-5">
      <!-- cover collage -->
      <div class="grid grid-cols-2 grid-rows-2 w-32 h-32 sm:w-36 sm:h-36 rounded-xl overflow-hidden border border-gray-200 dark:border-gray-700 shadow-sm shrink-0">
        <img v-for="(c, i) in four" :key="i" :src="c" alt="" class="w-full h-full object-cover" />
      </div>

      <div class="min-w-0 flex-1 flex flex-col">
        <span class="text-xs font-semibold uppercase tracking-wider text-blue-600 dark:text-blue-400">{{ t('album.eyebrow') }}</span>
        <h1 class="text-2xl sm:text-3xl font-bold text-gray-900 dark:text-white mt-1 break-words">{{ dirName }}</h1>
        <p v-if="sharedArtist" class="text-gray-600 dark:text-gray-300 mt-0.5 font-medium italic">{{ sharedArtist }}</p>
        <div class="mt-1.5 text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2 flex-wrap">
          <span class="font-semibold text-gray-700 dark:text-gray-200 tabular-nums">{{ t('album.tracks_count', { n: tracks.length }, tracks.length) }}</span>
          <span>·</span><span class="tabular-nums">{{ runtimeLabel }}</span>
          <template v-if="formats"><span>·</span><span>{{ formats }}</span></template>
        </div>

        <!-- Uniform icon-only transport. Play-all (sequential) and Shuffle are a
             mutually-exclusive mode pair: the solid blue fill sits on whichever
             is active. Repeat fills blue when on; repeat-one adds a "1" badge. -->
        <div class="mt-auto pt-5 flex items-center gap-2">
          <button
            @click="playAll"
            :title="t('album.play_all')"
            class="inline-flex items-center justify-center h-11 w-11 rounded-full transition-colors"
            :class="!shuffle ? activeBtn : ghostBtn"
          >
            <PlayIcon class="h-5 w-5" />
          </button>
          <button
            @click="shuffleAll"
            :title="t('album.shuffle')"
            class="inline-flex items-center justify-center h-11 w-11 rounded-full transition-colors"
            :class="shuffle ? activeBtn : ghostBtn"
          >
            <ShuffleIcon class="h-5 w-5" />
          </button>
          <button
            @click="cycleRepeat"
            :title="t('album.repeat')"
            class="relative inline-flex items-center justify-center h-11 w-11 rounded-full transition-colors"
            :class="repeat !== 'off' ? activeBtn : ghostBtn"
          >
            <ArrowPathIcon class="h-5 w-5" />
            <span v-if="repeat === 'one'" class="absolute -bottom-0.5 -right-0.5 h-4 w-4 rounded-full bg-white text-blue-600 text-[9px] font-bold flex items-center justify-center shadow-sm">1</span>
          </button>
        </div>
      </div>
    </div>

    <!-- recursive toggle strip -->
    <div class="px-6 py-3 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between gap-4 flex-wrap">
      <button
        @click="emit('update:recursive', !recursive)"
        class="flex items-center gap-2.5 text-left"
        role="switch"
        :aria-checked="recursive"
      >
        <span
          class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors"
          :class="recursive ? 'bg-blue-600' : 'bg-gray-300 dark:bg-gray-600'"
        >
          <span class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform" :class="recursive ? 'translate-x-4' : 'translate-x-0.5'" />
        </span>
        <span class="leading-tight">
          <span class="block text-sm font-medium text-gray-700 dark:text-gray-200">{{ t('album.include_subdirs') }}</span>
          <span class="block text-xs text-gray-400 dark:text-gray-500">{{ recursive ? t('album.include_subdirs_hint_on') : t('album.include_subdirs_hint_off') }}</span>
        </span>
      </button>
      <span class="text-xs text-gray-400 dark:text-gray-500 flex items-center gap-1.5">
        <MusicalNoteIcon class="h-4 w-4" />
        {{ t('album.audio_only_note') }}
      </span>
    </div>
  </div>
</template>
