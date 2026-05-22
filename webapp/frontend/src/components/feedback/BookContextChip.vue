<script setup lang="ts">
import { BookOpenIcon, XMarkIcon } from '@heroicons/vue/24/outline'

defineProps<{
  title: string
  author?: string | null
  removable?: boolean
}>()
defineEmits<{ (e: 'remove'): void }>()
</script>

<template>
  <span class="inline-flex items-center gap-2 max-w-full px-2.5 py-1 rounded-full bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 text-blue-800 dark:text-blue-200 text-xs">
    <BookOpenIcon class="h-3.5 w-3.5 shrink-0" />
    <span class="truncate font-medium">Re: {{ title }}</span>
    <span v-if="author" class="text-blue-700/70 dark:text-blue-300/70 truncate">— {{ author }}</span>
    <button
      v-if="removable"
      type="button"
      class="ml-0.5 -mr-0.5 opacity-60 hover:opacity-100"
      @click="$emit('remove')"
    >
      <XMarkIcon class="h-3 w-3" />
    </button>
  </span>
</template>
