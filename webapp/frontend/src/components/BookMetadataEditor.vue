<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api'

const { t } = useI18n({ useScope: 'global' })

type BookDetail = {
  id: string
  title: string | null
  author: string | null
  publisher: string | null
  published: string | null
  description: string | null
  tags: string | null
  series: string | null
  languages: string | null
  identifiers: string | null
  original_filename: string
  clearance: number
  needs_review: boolean
  locations: string[]
}

const props = defineProps<{ hashId: string | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', book: BookDetail): void
}>()

const editing = ref<BookDetail | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const load = async (id: string) => {
  loading.value = true
  error.value = ''
  editing.value = null
  try {
    const res = await api.get(`/admin/books/${encodeURIComponent(id)}`)
    editing.value = { ...res.data }
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

watch(
  () => props.hashId,
  (id) => {
    if (id) load(id)
    else editing.value = null
  },
  { immediate: true }
)

const close = () => emit('close')

const save = async () => {
  if (!editing.value) return
  saving.value = true
  error.value = ''
  try {
    const e = editing.value
    const payload = {
      title: e.title,
      author: e.author,
      publisher: e.publisher,
      published: e.published,
      description: e.description,
      tags: e.tags,
      series: e.series,
      languages: e.languages,
      identifiers: e.identifiers,
      clearance: e.clearance,
      needs_review: e.needs_review,
    }
    const res = await api.put(`/admin/books/${encodeURIComponent(e.id)}`, payload)
    emit('saved', res.data)
    emit('close')
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="hashId" class="fixed inset-0 z-50 flex items-start sm:items-center justify-center p-4">
    <div class="fixed inset-0 bg-black/40" @click="close"></div>
    <div class="relative w-full max-w-2xl bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 max-h-[90vh] overflow-y-auto">
      <div class="sticky top-0 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 p-4 flex items-center justify-between">
        <h3 class="text-lg font-semibold text-gray-900 dark:text-white">{{ t('admin.edit_book') }}</h3>
        <button @click="close" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">✕</button>
      </div>

      <div v-if="loading" class="p-6 text-sm text-gray-500 dark:text-gray-400">{{ t('admin.loading') }}</div>

      <div v-else-if="editing" class="p-4 space-y-3 text-sm">
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_hash') }}</label>
          <div class="font-mono text-xs break-all text-gray-700 dark:text-gray-300">{{ editing.id }}</div>
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_locations') }}</label>
          <ul class="text-xs text-gray-700 dark:text-gray-300">
            <li v-for="(loc, i) in editing.locations" :key="i" class="break-all">/{{ loc }}</li>
          </ul>
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_title') }}</label>
          <input v-model="editing.title" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_author') }}</label>
          <input v-model="editing.author" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_publisher') }}</label>
            <input v-model="editing.publisher" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_published') }}</label>
            <input v-model="editing.published" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_series') }}</label>
            <input v-model="editing.series" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_languages') }}</label>
            <input v-model="editing.languages" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_identifiers') }}</label>
            <input v-model="editing.identifiers" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
          </div>
          <div>
            <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_tags') }}</label>
            <input v-model="editing.tags" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
          </div>
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_description') }}</label>
          <textarea v-model="editing.description" rows="4" class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"></textarea>
        </div>
        <div class="flex items-center gap-4">
          <label class="block">
            <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('admin.field_clearance') }}</span>
            <input v-model.number="editing.clearance" type="number" min="0" class="ml-2 w-24 px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100" />
          </label>
          <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
            <input v-model="editing.needs_review" type="checkbox" /> {{ t('admin.field_needs_review') }}
          </label>
        </div>
        <div v-if="error" class="text-red-600 dark:text-red-400 text-sm">{{ error }}</div>
      </div>

      <div v-else-if="error" class="p-4 text-red-600 dark:text-red-400 text-sm">{{ error }}</div>

      <div class="sticky bottom-0 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 p-4 flex justify-end gap-2">
        <button @click="close" class="px-4 py-2 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 focus:outline-none">{{ t('admin.cancel') }}</button>
        <button
          @click="save"
          :disabled="saving || !editing"
          class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 focus:outline-none"
        >{{ saving ? t('admin.saving') : t('admin.save') }}</button>
      </div>
    </div>
  </div>
</template>
