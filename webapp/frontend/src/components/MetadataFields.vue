<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n({ useScope: 'global' })

export type MetadataModel = {
  title: string | null
  author: string | null
  publisher: string | null
  published: string | null
  description: string | null
  tags: string | null
  series: string | null
  languages: string | null
  identifiers: string | null
  [key: string]: any
}

const props = defineProps<{
  modelValue: MetadataModel
  showTitleError?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: MetadataModel): void
}>()

const update = <K extends keyof MetadataModel>(key: K, value: MetadataModel[K]) => {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

const tagChips = computed(() =>
  (props.modelValue.tags || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
)
</script>

<template>
  <div class="space-y-4">
    <div>
      <div class="flex items-baseline justify-between mb-1">
        <label class="text-xs font-medium text-gray-600 dark:text-gray-400">
          {{ t('admin.upload.field.title') }}
          <span class="text-red-500">*</span>
        </label>
        <span v-if="showTitleError" class="text-xs text-red-500">{{ t('admin.upload.review.title_required') }}</span>
      </div>
      <input
        :value="modelValue.title || ''"
        @input="update('title', ($event.target as HTMLInputElement).value)"
        class="w-full px-3 py-2 border rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
        :class="showTitleError ? 'border-red-400 dark:border-red-500' : 'border-gray-300 dark:border-gray-600'"
      />
    </div>

    <div>
      <div class="flex items-baseline justify-between mb-1">
        <label class="text-xs font-medium text-gray-600 dark:text-gray-400">{{ t('admin.upload.field.author') }}</label>
      </div>
      <input
        :value="modelValue.author || ''"
        @input="update('author', ($event.target as HTMLInputElement).value)"
        class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
      />
    </div>

    <div class="grid grid-cols-2 gap-3">
      <div>
        <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{ t('admin.upload.field.publisher') }}</label>
        <input
          :value="modelValue.publisher || ''"
          @input="update('publisher', ($event.target as HTMLInputElement).value)"
          class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
        />
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{ t('admin.upload.field.published') }}</label>
        <input
          :value="modelValue.published || ''"
          @input="update('published', ($event.target as HTMLInputElement).value)"
          :placeholder="t('admin.upload.field.published_placeholder')"
          class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
        />
      </div>
    </div>

    <div>
      <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{ t('admin.upload.field.description') }}</label>
      <textarea
        :value="modelValue.description || ''"
        @input="update('description', ($event.target as HTMLTextAreaElement).value)"
        rows="3"
        class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 resize-y focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
      ></textarea>
    </div>

    <div>
      <div class="flex items-baseline justify-between mb-1">
        <label class="text-xs font-medium text-gray-600 dark:text-gray-400">{{ t('admin.upload.field.tags') }}</label>
        <span class="text-xs text-gray-400 dark:text-gray-500">{{ t('admin.upload.field.tags_hint') }}</span>
      </div>
      <input
        :value="modelValue.tags || ''"
        @input="update('tags', ($event.target as HTMLInputElement).value)"
        class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
      />
      <div v-if="tagChips.length" class="flex flex-wrap gap-1.5 mt-2">
        <span
          v-for="(tag, i) in tagChips"
          :key="i"
          class="px-2 py-0.5 rounded-full text-xs bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300"
        >{{ tag }}</span>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-3">
      <div>
        <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{ t('admin.upload.field.series') }}</label>
        <input
          :value="modelValue.series || ''"
          @input="update('series', ($event.target as HTMLInputElement).value)"
          class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
        />
      </div>
      <div>
        <div class="flex items-baseline justify-between mb-1">
          <label class="text-xs font-medium text-gray-600 dark:text-gray-400">{{ t('admin.upload.field.languages') }}</label>
          <span class="text-xs text-gray-400 dark:text-gray-500">{{ t('admin.upload.field.languages_hint') }}</span>
        </div>
        <input
          :value="modelValue.languages || ''"
          @input="update('languages', ($event.target as HTMLInputElement).value)"
          class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
        />
      </div>
    </div>

    <div>
      <div class="flex items-baseline justify-between mb-1">
        <label class="text-xs font-medium text-gray-600 dark:text-gray-400">{{ t('admin.upload.field.identifiers') }}</label>
        <span class="text-xs text-gray-400 dark:text-gray-500">{{ t('admin.upload.field.identifiers_hint') }}</span>
      </div>
      <input
        :value="modelValue.identifiers || ''"
        @input="update('identifiers', ($event.target as HTMLInputElement).value)"
        placeholder="isbn:978-…"
        class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
      />
    </div>
  </div>
</template>
