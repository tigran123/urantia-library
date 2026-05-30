<script setup lang="ts">
// List row for one playlist item (book OR directory), mirroring the Browse list
// row. Owner mode shows a left drag handle + a right remove (trash) button and
// the clearance pill in the meta line; public mode is read-only (download only).
import {
  ArrowDownTrayIcon, TrashIcon, FolderIcon, DocumentIcon, Bars3Icon,
} from '@heroicons/vue/24/outline'
import StarRating from './StarRating.vue'
import QualityMark from './QualityMark.vue'
import { getFullUrl } from '../lib/assets'
import { usePlaylistItem } from '../composables/usePlaylistItem'
import type { PlaylistItem } from '../api'

const props = withDefaults(defineProps<{
  item: PlaylistItem
  mode: 'owner' | 'public'
  draggable?: boolean
  // See PlaylistBookCard for the rationale — opaque ?from=… tag on links.
  from?: string | null
}>(), { draggable: false, from: null })

const emit = defineEmits<{
  (e: 'remove', id: number): void
  (e: 'edit-clearance', item: PlaylistItem): void
}>()

// Shared with PlaylistBookCard — see usePlaylistItem.
const { t, isAdmin, isDir, isOwner, to, typeLabel, displayTitle, recTip, download } = usePlaylistItem(props)
</script>

<template>
  <div class="relative flex gap-4 items-start">
    <!-- drag handle (owner + draggable) -->
    <div
      v-if="isOwner && draggable"
      class="flex-shrink-0 self-center text-gray-300 dark:text-gray-600 cursor-grab"
      :title="t('playlists.reorder_hint')"
    >
      <Bars3Icon class="h-5 w-5" />
    </div>

    <!-- cover / icon -->
    <div class="flex-shrink-0">
      <div v-if="isDir" class="h-16 w-12 flex items-center justify-center bg-blue-50/50 dark:bg-gray-700/50 rounded shadow-sm border border-gray-200 dark:border-gray-700">
        <FolderIcon class="h-8 w-8 text-blue-400 dark:text-blue-500" />
      </div>
      <div v-else class="h-16 w-12 flex items-center justify-center bg-gray-100 dark:bg-gray-900 rounded shadow-sm overflow-hidden border border-gray-200 dark:border-gray-700 relative">
        <img v-if="item.cover_url" :src="getFullUrl(item.cover_url)" class="w-full h-full object-contain" alt="" />
        <DocumentIcon v-else class="h-6 w-6 text-gray-400 dark:text-gray-600" />
      </div>
    </div>

    <!-- details -->
    <div class="flex-1 min-w-0 pr-20">
      <component
        :is="to ? 'router-link' : 'span'"
        v-bind="to ? { to, draggable: 'false' } : {}"
        :class="['text-lg font-medium break-words', to ? 'text-blue-600 hover:underline' : 'text-gray-500 cursor-not-allowed']"
      >
        {{ displayTitle }}
      </component>
      <p v-if="item.author" class="text-sm text-gray-700 dark:text-gray-300 mt-0.5">{{ item.author }}</p>
      <p v-if="item.description" class="text-sm text-gray-600 dark:text-gray-400 mt-1 line-clamp-2" v-html="item.description"></p>

      <div v-if="!isDir" class="mt-2 text-xs text-gray-400 flex items-center gap-2 flex-wrap">
        <QualityMark
          v-if="item.is_recommended"
          class="h-4 w-4 text-green-600 dark:text-green-400"
          :title="recTip()"
        />
        <StarRating v-if="item.rating_count" :rating="item.avg_rating ?? null" :count="item.rating_count" />
        <span v-if="typeLabel" class="font-semibold">{{ typeLabel }}</span>
        <button
          v-if="isOwner && isAdmin"
          type="button"
          @click.prevent.stop="emit('edit-clearance', item)"
          class="px-1.5 py-0.5 rounded font-mono bg-amber-100 dark:bg-amber-900/70 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-700 hover:bg-amber-200 dark:hover:bg-amber-800"
          :title="t('admin.edit_clearance_tooltip')"
        >🔒 {{ item.clearance ?? 0 }}</button>
      </div>
    </div>

    <!-- right-side actions -->
    <div class="absolute right-0 top-0 flex items-center gap-1">
      <button
        v-if="!isDir && item.path"
        @click.prevent.stop="download($event)"
        class="p-1.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-blue-500 transition-colors"
        :title="t('app.download')"
      >
        <ArrowDownTrayIcon class="h-5 w-5" />
      </button>
      <button
        v-if="isOwner"
        @click.prevent.stop="emit('remove', item.id)"
        class="p-1.5 rounded-full hover:bg-red-50 dark:hover:bg-red-950/40 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-colors"
        :title="t('playlists.remove')"
      >
        <TrashIcon class="h-5 w-5" />
      </button>
    </div>
  </div>
</template>
