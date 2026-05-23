<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { GlobeAltIcon, LockClosedIcon } from '@heroicons/vue/24/outline'
import type { Annotation } from '../api'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{
  annotations: Annotation[]
}>()

const emit = defineEmits<{
  (e: 'jump', annotation: Annotation): void
}>()

const sorted = computed(() => {
  return [...props.annotations].sort((a, b) =>
    a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0,
  )
})
</script>

<template>
  <div class="flex flex-col h-full">
    <p v-if="!sorted.length" class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">
      {{ t('app.annotation_none') }}
    </p>
    <ul v-else class="flex flex-col text-sm">
      <li
        v-for="ann in sorted"
        :key="ann.id"
        @click="emit('jump', ann)"
        class="px-2 py-1.5 rounded cursor-pointer hover:bg-gray-200 dark:hover:bg-gray-700"
      >
        <div class="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
          <span :class="[
            'inline-block w-2.5 h-2.5 rounded-sm shrink-0',
            ann.is_own ? 'bg-yellow-400' : 'bg-orange-500',
          ]"></span>
          <span class="truncate">{{ t('app.annotation_by', { name: ann.author_name }) }}</span>
          <GlobeAltIcon v-if="ann.is_public" class="w-3 h-3 shrink-0" />
          <LockClosedIcon v-else class="w-3 h-3 shrink-0" />
          <span v-if="ann.is_public && ann.status === 'pending'" class="italic">
            {{ t('app.pending_moderation') }}
          </span>
        </div>
        <blockquote
          class="border-l-2 pl-2 mt-0.5 italic text-gray-700 dark:text-gray-300 line-clamp-2"
          :class="ann.is_own ? 'border-yellow-400' : 'border-orange-500'"
        >{{ ann.selected_text }}</blockquote>
        <p v-if="ann.body" class="mt-0.5 text-gray-900 dark:text-gray-100 line-clamp-3">{{ ann.body }}</p>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
