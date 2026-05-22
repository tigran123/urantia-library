<script setup lang="ts">
import { ref } from 'vue'
import { ChevronRightIcon } from '@heroicons/vue/24/outline'
import EpubTocNode from './EpubTocNode.vue'
import type { NavItem } from 'epubjs/types/navigation'

const props = defineProps<{
  entry: NavItem
  level: number
}>()

const emit = defineEmits<{ (e: 'navigate', href: string): void }>()

const expanded = ref(props.level < 1)

const hasChildren = () => !!props.entry.subitems && props.entry.subitems.length > 0
const toggle = (e: MouseEvent) => {
  e.stopPropagation()
  if (hasChildren()) expanded.value = !expanded.value
}
const navigate = () => {
  if (props.entry.href) emit('navigate', props.entry.href)
}
</script>

<template>
  <div class="epub-toc-node">
    <div
      class="flex items-start gap-1 py-1 pr-2 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-sm cursor-pointer"
      :style="{ paddingLeft: `${level * 12 + 4}px` }"
      @click="navigate"
    >
      <button
        v-if="hasChildren()"
        @click="toggle"
        class="shrink-0 mt-0.5 w-4 h-4 flex items-center justify-center text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
        :aria-expanded="expanded"
      >
        <ChevronRightIcon class="w-3 h-3 transition-transform" :class="{ 'rotate-90': expanded }" />
      </button>
      <span v-else class="shrink-0 w-4" aria-hidden="true" />
      <span class="leading-snug line-clamp-2 break-words" :title="entry.label">
        {{ entry.label || '—' }}
      </span>
    </div>
    <div v-if="expanded && hasChildren()">
      <EpubTocNode
        v-for="(child, i) in entry.subitems"
        :key="i"
        :entry="child"
        :level="level + 1"
        @navigate="(h) => emit('navigate', h)"
      />
    </div>
  </div>
</template>
