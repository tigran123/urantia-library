<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'
import { DocumentIcon } from '@heroicons/vue/24/outline'
import DjvuViewer from '../DjvuViewer.vue'
import EpubViewer from '../EpubViewer.vue'
import Fb2Viewer from '../Fb2Viewer.vue'
import MdViewer from '../MdViewer.vue'
import HtmlViewer from '../HtmlViewer.vue'
import ImageViewer from '../ImageViewer.vue'
import api from '../../api'
// PdfViewer pulls in ~1MB of pdfjs-dist; only fetch it once the user expands
// this panel and actually has a PDF to preview.
const PdfViewer = defineAsyncComponent(() => import('../PdfViewer.vue'))

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{
  stagingId: string
  filename: string
}>()

const source = computed(() => ({
  kind: 'staging' as const,
  stagingId: props.stagingId,
  filename: props.filename,
}))

const lower = computed(() => (props.filename || '').toLowerCase())

const isPdf = computed(() => lower.value.endsWith('.pdf'))
const isDjvu = computed(() => lower.value.endsWith('.djvu'))
const isEpub = computed(() => lower.value.endsWith('.epub'))
const isFb2 = computed(() => lower.value.endsWith('.fb2') || lower.value.endsWith('.fb2.zip'))
const isMd = computed(() => lower.value.endsWith('.md') || lower.value.endsWith('.markdown')
  || lower.value.endsWith('.md.zip') || lower.value.endsWith('.markdown.zip'))
const isTxt = computed(() => lower.value.endsWith('.txt') || lower.value.endsWith('.txt.zip'))
const isHtml = computed(() => lower.value.endsWith('.html') || lower.value.endsWith('.htm')
  || lower.value.endsWith('.html.zip') || lower.value.endsWith('.htm.zip'))
const isImage = computed(() => /\.(jpe?g|png|gif|webp|svg)$/i.test(lower.value))
const isAudio = computed(() => /\.(mp3|wav|ogg|flac|m4a|aac)$/i.test(lower.value))
const isVideo = computed(() => /\.(mp4|webm|mkv|avi|mov)$/i.test(lower.value))

// Build a full URL (including baseURL) for the <img> tag, since axios doesn't
// proxy <img>. Matches ItemView.getDownloadUrl()'s strategy.
const imageSrc = computed(() => {
  const base = (api.defaults.baseURL || '/api').replace(/\/api$/, '')
  return `${base}/api/admin/books/upload/${encodeURIComponent(props.stagingId)}/file`
})
</script>

<template>
  <div class="w-full">
    <PdfViewer v-if="isPdf" :source="source" embedded />
    <DjvuViewer v-else-if="isDjvu" :source="source" embedded />
    <EpubViewer v-else-if="isEpub" :source="source" embedded />
    <Fb2Viewer v-else-if="isFb2" :source="source" embedded />
    <MdViewer v-else-if="isMd || isTxt" :source="source" embedded />
    <HtmlViewer v-else-if="isHtml" :source="source" embedded />
    <ImageViewer v-else-if="isImage" :src="imageSrc" />
    <audio v-else-if="isAudio" controls class="w-full" :src="imageSrc" />
    <video v-else-if="isVideo" controls class="w-full max-h-[60vh]" :src="imageSrc" />
    <div v-else class="p-8 text-center text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/40 rounded">
      <DocumentIcon class="mx-auto h-12 w-12 mb-2 text-gray-300 dark:text-gray-600" />
      <p class="text-sm">{{ t('admin.upload.review.preview_unsupported') }}</p>
    </div>
  </div>
</template>
