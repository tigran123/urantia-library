<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  XMarkIcon,
  ExclamationTriangleIcon,
  ArrowPathIcon,
  ClipboardIcon,
  CheckIcon,
} from '@heroicons/vue/24/outline'
import api from '../api'
import MetadataFields from './MetadataFields.vue'
import ClearanceControl from './ClearanceControl.vue'
import CoverPreview from './CoverPreview.vue'

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
  cover_url?: string | null
}

const props = defineProps<{ hashId: string | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', book: BookDetail): void
}>()

const editing = ref<BookDetail | null>(null)
const loading = ref(false)
const saving = ref(false)
const reextracting = ref(false)
const error = ref('')
const coverFile = ref<File | null>(null)
const hashCopied = ref(false)

const titleMissing = computed(() => !(editing.value?.title || '').trim())

const load = async (id: string) => {
  loading.value = true
  error.value = ''
  editing.value = null
  coverFile.value = null
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
    else { editing.value = null; coverFile.value = null }
  },
  { immediate: true }
)

const close = () => emit('close')

const copyHash = async () => {
  if (!editing.value) return
  try {
    await navigator.clipboard.writeText(editing.value.id)
    hashCopied.value = true
    setTimeout(() => { hashCopied.value = false }, 1500)
  } catch { /* ignore */ }
}

const save = async () => {
  if (!editing.value || titleMissing.value) return
  saving.value = true
  error.value = ''
  try {
    const e = editing.value
    if (coverFile.value) {
      const form = new FormData()
      form.append('file', coverFile.value)
      const cres = await api.put(`/admin/books/${encodeURIComponent(e.id)}/cover`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      e.cover_url = cres.data?.cover_url || e.cover_url
    }
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

const reextractCover = async () => {
  if (!editing.value) return
  reextracting.value = true
  error.value = ''
  try {
    const res = await api.post(`/admin/books/${encodeURIComponent(editing.value.id)}/cover/reextract`)
    editing.value.cover_url = res.data?.cover_url || editing.value.cover_url
    coverFile.value = null
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    reextracting.value = false
  }
}
</script>

<template>
  <div v-if="hashId" class="fixed inset-0 z-50 flex items-start sm:items-center justify-center p-4">
    <div class="fixed inset-0 bg-black/40" @click="close"></div>
    <div class="relative w-full max-w-2xl bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 max-h-[90vh] overflow-y-auto">
      <div class="sticky top-0 bg-white dark:bg-gray-800 border-b border-gray-100 dark:border-gray-700 p-4 flex items-center justify-between z-10">
        <h3 class="text-lg font-semibold text-gray-900 dark:text-white">{{ t('admin.edit_book') }}</h3>
        <button @click="close" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
          <XMarkIcon class="w-5 h-5" />
        </button>
      </div>

      <div v-if="loading" class="p-6 text-sm text-gray-500 dark:text-gray-400">{{ t('admin.loading') }}</div>

      <template v-else-if="editing">
        <div class="px-6 py-3 bg-gray-50 dark:bg-gray-900/40 border-b border-gray-100 dark:border-gray-700 text-xs space-y-1">
          <div class="flex items-baseline gap-3">
            <span class="text-gray-500 dark:text-gray-400 shrink-0">{{ t('admin.field_hash') }}</span>
            <code class="font-mono text-gray-700 dark:text-gray-300 break-all">{{ editing.id }}</code>
            <button
              @click="copyHash"
              class="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 shrink-0"
              :title="hashCopied ? 'Copied' : 'Copy'"
            >
              <CheckIcon v-if="hashCopied" class="w-3.5 h-3.5 text-emerald-500" />
              <ClipboardIcon v-else class="w-3.5 h-3.5" />
            </button>
          </div>
          <div class="flex items-baseline gap-3 flex-wrap">
            <span class="text-gray-500 dark:text-gray-400 shrink-0">{{ t('admin.field_locations') }}</span>
            <code v-for="loc in editing.locations" :key="loc" class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-[11px] break-all">/{{ loc }}</code>
          </div>
        </div>

        <div class="p-6 grid grid-cols-[200px_1fr] gap-6">
          <div>
            <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.cover') }}</div>
            <CoverPreview
              :image-url="editing.cover_url"
              :file="coverFile"
              size="full"
              @update:file="(f) => coverFile = f"
              @re-extract="reextractCover"
            />
            <div v-if="reextracting" class="mt-2 inline-flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
              <ArrowPathIcon class="w-3.5 h-3.5 animate-spin" />
              <span>{{ t('admin.upload.review.reextract') }}…</span>
            </div>
          </div>
          <div>
            <MetadataFields v-model="editing" :show-title-error="titleMissing" />
          </div>
        </div>

        <div class="px-6 pb-6">
          <div class="border-t border-gray-100 dark:border-gray-700 pt-5">
            <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.access') }}</div>
            <div class="grid grid-cols-2 gap-6">
              <div>
                <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{ t('admin.upload.review.clearance') }}</label>
                <ClearanceControl v-model="editing.clearance" />
              </div>
              <div>
                <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                  <input v-model="editing.needs_review" type="checkbox" class="rounded" />
                  <span>{{ t('admin.field_needs_review') }}</span>
                </label>
              </div>
            </div>
          </div>
        </div>

        <div v-if="error" class="mx-6 mb-4 text-red-600 dark:text-red-400 text-sm">{{ error }}</div>
      </template>

      <div v-else-if="error" class="p-4 text-red-600 dark:text-red-400 text-sm">{{ error }}</div>

      <div class="sticky bottom-0 bg-white dark:bg-gray-800 border-t border-gray-100 dark:border-gray-700 p-4 flex items-center justify-between">
        <div class="text-xs text-gray-500 dark:text-gray-400">
          <span v-if="coverFile">{{ t('admin.cover.queued', { size: Math.round(coverFile.size / 1024) }) }}</span>
        </div>
        <div class="flex items-center gap-3">
          <div v-if="titleMissing && editing" class="inline-flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400">
            <ExclamationTriangleIcon class="w-4 h-4" />
            {{ t('admin.upload.review.title_required') }}
          </div>
          <button @click="close" class="px-4 py-2 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 text-sm hover:bg-gray-50 dark:hover:bg-gray-700">{{ t('admin.cancel') }}</button>
          <button
            @click="save"
            :disabled="saving || !editing || titleMissing"
            class="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            <ArrowPathIcon v-if="saving" class="w-4 h-4 animate-spin" />
            <span>{{ saving ? t('admin.saving') : t('admin.save') }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
