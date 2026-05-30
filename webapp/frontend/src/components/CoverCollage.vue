<script setup lang="ts">
// Auto 2x2 cover collage from the first up-to-4 playlist items. Fills its
// parent (the parent controls the aspect ratio / size). 1 item = full bleed,
// 3 items = first spans two rows, empty = centered outline bookmark. Directory
// items render a folder tile, book items their cover (or a document glyph).
import { computed } from 'vue'
import { BookmarkIcon, FolderIcon, DocumentIcon } from '@heroicons/vue/24/outline'
import { getFullUrl } from '../lib/assets'
import type { CollageItem } from '../api'

const props = defineProps<{ items: CollageItem[] }>()
const shown = computed(() => (props.items || []).slice(0, 4))
const n = computed(() => shown.value.length)
</script>

<template>
  <div class="w-full h-full bg-blue-50/40 dark:bg-gray-900/60">
    <div v-if="n === 0" class="w-full h-full flex items-center justify-center">
      <BookmarkIcon class="h-1/3 w-1/3 text-blue-300 dark:text-blue-700" />
    </div>
    <div
      v-else
      class="w-full h-full grid gap-px"
      :class="[
        n === 1 ? 'grid-cols-1' : 'grid-cols-2',
        n >= 3 ? 'grid-rows-2' : 'grid-rows-1',
      ]"
    >
      <div
        v-for="(it, i) in shown"
        :key="i"
        :class="[
          'relative overflow-hidden bg-gray-100 dark:bg-gray-800 flex items-center justify-center',
          n === 3 && i === 0 ? 'row-span-2' : '',
        ]"
      >
        <img
          v-if="it.item_type === 'book' && it.cover_url"
          :src="getFullUrl(it.cover_url)"
          class="w-full h-full object-cover"
          alt=""
        />
        <FolderIcon
          v-else-if="it.item_type === 'directory'"
          class="h-1/2 w-1/2 text-blue-400 dark:text-blue-500"
        />
        <DocumentIcon v-else class="h-1/2 w-1/2 text-gray-300 dark:text-gray-600" />
      </div>
    </div>
  </div>
</template>
