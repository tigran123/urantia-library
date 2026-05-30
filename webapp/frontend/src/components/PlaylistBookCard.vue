<script setup lang="ts">
// Grid card for one playlist item (book OR directory), mirroring the Browse
// grid card. Owner mode adds the clearance pill, a remove (x) button, and a
// hover drag grip; public mode is read-only (download only). The parent owns
// the HTML5 drag wiring — this card only renders the grip affordance.
import {
  ArrowDownTrayIcon, XMarkIcon, FolderIcon, DocumentIcon, Bars3Icon,
} from '@heroicons/vue/24/outline'
import StarRating from './StarRating.vue'
import QualityMark from './QualityMark.vue'
import { gridCls } from '../composables/useGridItemSize'
import { getFullUrl } from '../lib/assets'
import { usePlaylistItem } from '../composables/usePlaylistItem'
import type { PlaylistItem } from '../api'

const props = withDefaults(defineProps<{
  item: PlaylistItem
  mode: 'owner' | 'public'
  draggable?: boolean
  // Opaque source tag (e.g. "playlist:42") carried as ?from=… on book/dir
  // links so destination views can navigate back here on context-relevant
  // actions (currently: deleting a book returns to the playlist, not parent).
  from?: string | null
}>(), { draggable: false, from: null })

const emit = defineEmits<{
  (e: 'remove', id: number): void
  (e: 'edit-clearance', item: PlaylistItem): void
}>()

// Nav target, type badge, title, recommendation tooltip, admin gate and
// blob-download are shared with PlaylistBookRow — see usePlaylistItem.
const { t, isAdmin, isDir, isOwner, to, typeLabel, displayTitle, recTip, download } = usePlaylistItem(props)
</script>

<template>
  <div class="relative group">
    <!-- znak качества (recommended) -->
    <span
      v-if="!isDir && item.is_recommended"
      :class="['absolute top-2 left-2 z-10 inline-flex items-center justify-center rounded-full bg-white/80 dark:bg-gray-800/80 shadow-sm backdrop-blur-sm border border-gray-100 dark:border-gray-600', gridCls.iconBtn]"
      :title="recTip()"
    >
      <QualityMark :class="[gridCls.icon, 'text-green-600 dark:text-green-400']" />
    </span>

    <!-- clearance pill (owner + admin only — shows what gates recipients).
         Clicking opens the same prompt the Browse/Search pill does. -->
    <button
      v-if="isOwner && !isDir && isAdmin"
      type="button"
      @click.prevent.stop="emit('edit-clearance', item)"
      :class="['absolute top-2 left-1/2 -translate-x-1/2 z-10 rounded font-mono bg-amber-100 dark:bg-amber-900/70 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-700 hover:bg-amber-200 dark:hover:bg-amber-800', gridCls.badge]"
      :title="t('admin.edit_clearance_tooltip')"
    >🔒 {{ item.clearance ?? 0 }}</button>

    <!-- remove (owner only) -->
    <button
      v-if="isOwner"
      @click.prevent.stop="emit('remove', item.id)"
      :class="['absolute top-2 right-2 z-10 rounded-full bg-white/80 dark:bg-gray-800/80 hover:bg-red-50 dark:hover:bg-red-950/40 text-gray-400 hover:text-red-500 dark:hover:text-red-400 shadow-sm backdrop-blur-sm transition-colors border border-gray-100 dark:border-gray-600', gridCls.iconBtn]"
      :title="t('playlists.remove')"
    >
      <XMarkIcon :class="gridCls.icon" />
    </button>

    <!-- drag grip (owner + draggable, hover only) -->
    <span
      v-if="isOwner && draggable"
      :class="['absolute bottom-[4.5rem] left-1/2 -translate-x-1/2 z-10 rounded bg-white/80 dark:bg-gray-800/80 text-gray-400 shadow-sm backdrop-blur-sm border border-gray-100 dark:border-gray-600 opacity-0 group-hover:opacity-100 transition-opacity cursor-grab', gridCls.iconBtn]"
      :title="t('playlists.reorder_hint')"
    >
      <Bars3Icon :class="gridCls.icon" />
    </span>

    <component
      :is="to ? 'router-link' : 'div'"
      v-bind="to ? { to, draggable: 'false' } : {}"
      :class="['flex flex-col items-center bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 hover:shadow-md transition-all hover:border-blue-300 dark:hover:border-blue-500', gridCls.card, !to && 'cursor-not-allowed opacity-70']"
    >
      <div :class="['aspect-[3/4] w-full rounded-lg overflow-hidden flex items-center justify-center bg-gray-50 dark:bg-gray-900 relative', gridCls.coverMargin]">
        <template v-if="isDir">
          <FolderIcon :class="[gridCls.bigIcon, 'text-blue-400 dark:text-blue-500']" />
        </template>
        <template v-else>
          <img
            v-if="item.cover_url"
            :src="getFullUrl(item.cover_url)"
            :alt="displayTitle"
            class="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300"
          />
          <DocumentIcon v-else :class="[gridCls.bigIcon, 'text-gray-300 dark:text-gray-600']" />
          <button
            v-if="item.path"
            @click.prevent.stop="download($event)"
            :class="['absolute bottom-2 left-2 z-10 rounded-full bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-700 shadow-sm backdrop-blur-sm border border-gray-100 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:text-blue-500', gridCls.iconBtn]"
            :title="t('app.download')"
          >
            <ArrowDownTrayIcon :class="gridCls.icon" />
          </button>
          <span
            v-if="typeLabel"
            :class="['absolute bottom-2 right-2 z-10 rounded font-mono font-semibold bg-gray-800/80 text-white backdrop-blur-sm', gridCls.badge]"
          >{{ typeLabel }}</span>
        </template>
      </div>
      <h3 :class="[gridCls.title, 'font-medium text-gray-900 dark:text-gray-100 text-center w-full break-words line-clamp-2']" :title="displayTitle">{{ displayTitle }}</h3>
      <p v-if="item.author" :class="[gridCls.subtitle, 'text-gray-500 dark:text-gray-400 mt-1 text-center w-full truncate font-bold italic']" :title="item.author">{{ item.author }}</p>
      <StarRating v-if="item.rating_count" :rating="item.avg_rating ?? null" :count="item.rating_count" class="mt-1" />
    </component>
  </div>
</template>
