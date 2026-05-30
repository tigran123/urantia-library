<script setup lang="ts">
// One card on the /playlists index: cover collage, visibility badge, "Default"
// pill on the Bookshelf, name + description, item count, updated date.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'
import CoverCollage from './CoverCollage.vue'
import VisibilityBadge from './VisibilityBadge.vue'
import { formatShortDate } from '../lib/itemFormat'
import type { PlaylistSummary } from '../api'

const props = defineProps<{ playlist: PlaylistSummary }>()
const { t, locale } = useI18n({ useScope: 'global' })

// A freshly-migrated Bookshelf keeps the literal name "Bookshelf"; show a
// localized label for it. If the owner renamed it, show their custom name.
const displayName = computed(() =>
  props.playlist.is_bookshelf && props.playlist.name === 'Bookshelf'
    ? t('playlists.bookshelf_name')
    : props.playlist.name,
)
</script>

<template>
  <router-link
    :to="`/playlists/${playlist.id}`"
    class="group flex flex-col h-full bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 hover:shadow-md hover:border-blue-300 dark:hover:border-blue-500 transition-all overflow-hidden"
  >
    <div class="relative aspect-[16/10] overflow-hidden">
      <CoverCollage :items="playlist.collage" />
      <div class="absolute top-2 left-2 z-10">
        <VisibilityBadge :visibility="playlist.visibility" />
      </div>
      <span
        v-if="playlist.is_bookshelf"
        class="absolute top-2 right-2 z-10 inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-white/90 dark:bg-gray-900/80 text-gray-700 dark:text-gray-200 border border-gray-200 dark:border-gray-700 shadow-sm"
      >
        <BookmarkIconSolid class="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" />
        {{ t('playlists.default_badge') }}
      </span>
    </div>
    <div class="p-4 flex flex-col flex-1">
      <h3 class="text-base font-semibold text-gray-900 dark:text-gray-100 line-clamp-1" :title="displayName">
        {{ displayName }}
      </h3>
      <!-- Reserve room for up to 2 description lines so the meta row sits at
           the same vertical position across every card (easier scanning). -->
      <p
        class="text-sm text-gray-500 dark:text-gray-400 mt-1 line-clamp-2 min-h-[2.5rem]"
        :title="playlist.description || ''"
      >
        {{ playlist.description || '' }}
      </p>
      <div class="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between text-sm mt-auto">
        <span class="font-semibold text-gray-700 dark:text-gray-200">
          {{ t('playlists.items_count', { n: playlist.item_count }, playlist.item_count) }}
        </span>
        <span class="text-gray-500 dark:text-gray-400">
          {{ t('playlists.updated') }} {{ formatShortDate(locale, playlist.updated_at) }}
        </span>
      </div>
    </div>
  </router-link>
</template>
