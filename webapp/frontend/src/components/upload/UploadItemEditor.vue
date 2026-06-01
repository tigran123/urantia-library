<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CheckIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
} from '@heroicons/vue/24/outline'
import MetadataFields from '../MetadataFields.vue'
import ClearanceControl from '../ClearanceControl.vue'
import CoverPreview from '../CoverPreview.vue'
import DirectoryTreePicker from './DirectoryTreePicker.vue'
import StagingPreview from './StagingPreview.vue'
import { type UploadItem, sourceName } from './uploadTypes'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{
  item: UploadItem
  single: boolean
}>()

const emit = defineEmits<{
  (e: 'commit'): void
  (e: 'remove'): void
  (e: 'view-log'): void
}>()

const previewOpen = ref(true)

const item = computed(() => props.item)
const committing = computed(() => props.item.status === 'committing')

const titleMissing = computed(() => !(props.item.meta.title || '').trim())

const destinationEmpty = computed(() => {
  const a = (props.item.selectedDir || '').replace(/^\/+|\/+$/g, '')
  const b = (props.item.extraSubpath || '').replace(/^\/+|\/+$/g, '')
  return !a && !b
})

const originalExt = computed(() => {
  const n = (props.item.stagingFilename || '').toLowerCase()
  if (n.endsWith('.fb2.zip')) return '.fb2.zip'
  const i = n.lastIndexOf('.')
  return i >= 0 ? n.slice(i) : ''
})

const filenameMismatch = computed(() => {
  if (!props.item.filename || !originalExt.value) return false
  return !props.item.filename.toLowerCase().endsWith(originalExt.value)
})

const reviewCoverMeta = computed(() => {
  if (!props.item.stagingCoverUrl) return ''
  return t('admin.upload.review.cover_meta', { w: '—', h: '—' })
})

const fmtBytes = (n: number) => {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let f = n / 1024, i = 0
  while (f >= 1024 && i < units.length - 1) { f /= 1024; i++ }
  return `${f.toFixed(1)} ${units[i]}`
}
</script>

<template>
  <div class="space-y-4">
    <!-- extracted metadata card -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
      <div class="px-6 py-3 bg-emerald-50 dark:bg-emerald-900/20 border-b border-emerald-200 dark:border-emerald-900/60 flex items-center gap-3">
        <div class="w-7 h-7 rounded-full bg-emerald-500 text-white flex items-center justify-center">
          <CheckIcon class="w-4 h-4" />
        </div>
        <div class="flex-1 min-w-0">
          <div class="text-sm font-semibold text-emerald-800 dark:text-emerald-200">{{ t('admin.upload.review.extracted') }}</div>
          <div class="font-mono text-xs text-emerald-700 dark:text-emerald-300 truncate">
            {{ sourceName(item.source) }} · {{ item.format.toUpperCase() }} · {{ fmtBytes(item.size) }} · {{ item.hash?.slice(0, 16) }}…
          </div>
        </div>
        <button
          @click="emit('view-log')"
          class="text-xs text-emerald-700 dark:text-emerald-300 underline hover:no-underline"
        >{{ t('admin.upload.log.view') }}</button>
      </div>

      <div class="p-6 grid grid-cols-[200px_1fr] gap-6">
        <div>
          <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.cover') }}</div>
          <CoverPreview
            :image-url="item.stagingCoverUrl"
            :file="item.coverOverride"
            size="full"
            :meta="reviewCoverMeta"
            @update:file="(f: File | null) => item.coverOverride = f"
          />
        </div>
        <div>
          <MetadataFields v-model="item.meta" :show-title-error="titleMissing" />
        </div>
      </div>
    </div>

    <!-- built-in preview -->
    <div v-if="item.stagingId" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
      <button
        type="button"
        @click="previewOpen = !previewOpen"
        class="w-full flex items-center justify-between px-6 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-700/40"
      >
        <span class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400">{{ t('admin.upload.review.preview') }}</span>
        <span class="inline-flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
          <span v-if="!previewOpen">{{ t('admin.upload.review.preview_open') }}</span>
          <span v-else>{{ t('admin.upload.review.preview_close') }}</span>
          <ChevronDownIcon v-if="previewOpen" class="w-4 h-4" />
          <ChevronRightIcon v-else class="w-4 h-4" />
        </span>
      </button>
      <div v-if="previewOpen" class="px-3 pb-3">
        <StagingPreview :staging-id="item.stagingId" :filename="item.filename" />
      </div>
    </div>

    <!-- destination + filename + access -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6 space-y-5">
      <div>
        <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.destination') }}</div>
        <DirectoryTreePicker
          v-model:selected-dir="item.selectedDir"
          v-model:extra-subpath="item.extraSubpath"
          :filename="item.filename"
        />
        <div
          v-if="destinationEmpty"
          class="mt-3 flex items-start gap-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-4 py-3 text-sm"
        >
          <ExclamationTriangleIcon class="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <div>
            <div class="font-semibold text-amber-800 dark:text-amber-200">{{ t('admin.upload.review.destination_empty.title') }}</div>
            <div class="text-xs text-amber-700 dark:text-amber-300 mt-0.5">{{ t('admin.upload.review.destination_empty.body') }}</div>
          </div>
        </div>
      </div>

      <div class="border-t border-gray-100 dark:border-gray-700 pt-5">
        <label class="block text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.filename') }}</label>
        <input
          v-model="item.filename"
          type="text"
          class="w-full px-3 py-2 border rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          :class="filenameMismatch ? 'border-amber-400 dark:border-amber-700' : 'border-gray-300 dark:border-gray-600'"
        />
        <p class="mt-1 text-xs" :class="filenameMismatch ? 'text-amber-600 dark:text-amber-400' : 'text-gray-500 dark:text-gray-400'">
          {{ t('admin.upload.review.filename_help', { ext: originalExt }) }}
        </p>
      </div>

      <div class="border-t border-gray-100 dark:border-gray-700 pt-5">
        <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.access') }}</div>
        <div class="grid grid-cols-2 gap-6">
          <div>
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{ t('admin.upload.review.clearance') }}</label>
            <ClearanceControl v-model="item.clearance" />
          </div>
          <div>
            <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <input v-model="item.needsReview" type="checkbox" class="rounded" />
              <span v-html="t('admin.upload.review.needs_review', { flag: '<code class=\'font-mono text-xs\'>needs_review</code>' })"></span>
            </label>
            <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ t('admin.upload.review.needs_review_help') }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- per-item footer -->
    <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-4 flex items-center justify-between">
      <button
        @click="emit('remove')"
        :disabled="committing"
        class="px-4 py-2 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
      >{{ single ? t('admin.upload.review.cancel') : t('admin.upload.batch.remove') }}</button>
      <div class="flex items-center gap-3">
        <div v-if="item.errorMsg" class="inline-flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400 max-w-xs truncate">
          <ExclamationTriangleIcon class="w-4 h-4 shrink-0" />
          {{ item.errorMsg }}
        </div>
        <div v-else-if="titleMissing" class="inline-flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400">
          <ExclamationTriangleIcon class="w-4 h-4" />
          {{ t('admin.upload.review.title_required') }}
        </div>
        <button
          @click="emit('commit')"
          :disabled="titleMissing || committing"
          class="inline-flex items-center gap-2 px-4 py-2 rounded bg-blue-600 text-white text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          <ArrowPathIcon v-if="committing" class="w-4 h-4 animate-spin" />
          <span>{{ committing ? t('admin.upload.review.committing') : (single ? t('admin.upload.review.commit') : t('admin.upload.batch.commit_this')) }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
