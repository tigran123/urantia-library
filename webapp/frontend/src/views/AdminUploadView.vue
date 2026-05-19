<script setup lang="ts">
import { ref, computed, inject, onMounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  ArrowUpTrayIcon,
  CheckIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  ArrowTopRightOnSquareIcon,
  XMarkIcon,
  ChevronDownIcon,
  ChevronRightIcon,
} from '@heroicons/vue/24/outline'
import api from '../api'
import AdminNav from '../components/AdminNav.vue'
import MetadataFields from '../components/MetadataFields.vue'
import ClearanceControl from '../components/ClearanceControl.vue'
import ClearancePill from '../components/ClearancePill.vue'
import CoverPreview from '../components/CoverPreview.vue'
import DestinationPicker from '../components/upload/DestinationPicker.vue'

const { t } = useI18n({ useScope: 'global' })

type CurrentUser = { email: string, is_admin?: boolean } | null
const currentUser = inject<{ value: CurrentUser } | null>('currentUser', null)
const router = useRouter()

type Stage = 'idle' | 'uploading' | 'duplicate' | 'review' | 'committing' | 'done'
type LogEntry = { time: string, level: 'info' | 'ok' | 'warn' | 'error', msg: string }

type Metadata = {
  title: string | null
  author: string | null
  publisher: string | null
  published: string | null
  description: string | null
  tags: string | null
  series: string | null
  languages: string | null
  identifiers: string | null
}

const DEFAULT_META: Metadata = {
  title: '', author: '', publisher: '', published: '',
  description: '', tags: '', series: '', languages: '', identifiers: '',
}

type ExistingBook = {
  id: string
  title: string | null
  author: string | null
  clearance: number
  locations: string[]
  cover_url?: string | null
}

type CommittedBook = ExistingBook & {
  original_filename: string
}

const ACCEPTED = ['FB2', 'FB2.ZIP', 'EPUB', 'PDF', 'DJVU', 'MOBI', 'AZW', 'AZW3', 'PRC', 'DOCX', 'ODT', 'RTF', 'HTML', 'TXT', 'TXT.ZIP', 'JPG', 'JPEG']
const ACCEPT_ATTR = '.fb2,.zip,.epub,.pdf,.djvu,.mobi,.azw,.azw3,.prc,.docx,.odt,.rtf,.html,.txt,.jpg,.jpeg'
const MAX_UPLOAD_BYTES = 850 * 1024 * 1024

const stage = ref<Stage>('idle')
const progress = ref(0)
const currentMsg = ref('')
const log = ref<LogEntry[]>([])
const showLogModal = ref(false)
const logExpanded = ref(true)
const dragOver = ref(false)
const errorMsg = ref('')

const file = ref<File | null>(null)
const stagingId = ref<string | null>(null)
const fileHash = ref<string | null>(null)
const fileFormat = ref<string>('')
const fileSize = ref<number>(0)
const meta = ref<Metadata>({ ...DEFAULT_META })
const topDir = ref<string>('')
const subpath = ref<string>('')
const clearance = ref<number>(100)
const needsReview = ref<boolean>(false)
const coverOverride = ref<File | null>(null)
const stagingCoverUrl = ref<string | null>(null)
const existingBook = ref<ExistingBook | null>(null)
const committedBook = ref<CommittedBook | null>(null)
const topDirs = ref<string[]>([])

const fileInputEl = ref<HTMLInputElement | null>(null)
const logEl = ref<HTMLElement | null>(null)
let abortController: AbortController | null = null

const stepIndex = computed(() => {
  switch (stage.value) {
    case 'idle': return 0
    case 'uploading':
    case 'duplicate': return 1
    case 'review': return 2
    case 'committing': return 3
    case 'done': return 4
  }
})

const titleMissing = computed(() => !(meta.value.title || '').trim())

const fmtBytes = (n: number) => {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let f = n / 1024, i = 0
  while (f >= 1024 && i < units.length - 1) { f /= 1024; i++ }
  return `${f.toFixed(1)} ${units[i]}`
}

const loadTopDirs = async () => {
  try {
    const res = await api.get('/admin/top-dirs')
    topDirs.value = res.data?.tops || []
    if (!topDir.value && topDirs.value.length) topDir.value = topDirs.value[0]
  } catch {
    topDirs.value = []
  }
}

onMounted(async () => {
  if (!currentUser?.value?.is_admin) {
    router.replace('/')
    return
  }
  await loadTopDirs()
})

watch(log, () => {
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}, { deep: true })

const resetToIdle = () => {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  if (stagingId.value) {
    api.delete(`/admin/books/upload/${stagingId.value}`).catch(() => {})
  }
  stage.value = 'idle'
  progress.value = 0
  currentMsg.value = ''
  log.value = []
  file.value = null
  stagingId.value = null
  fileHash.value = null
  fileFormat.value = ''
  fileSize.value = 0
  meta.value = { ...DEFAULT_META }
  subpath.value = ''
  clearance.value = 100
  needsReview.value = false
  coverOverride.value = null
  stagingCoverUrl.value = null
  existingBook.value = null
  committedBook.value = null
  errorMsg.value = ''
}

const validateExt = (name: string): boolean => {
  const lower = name.toLowerCase()
  if (lower.endsWith('.fb2.zip')) return true
  return ACCEPT_ATTR.split(',').some((ext) => lower.endsWith(ext))
}

const startUpload = async (chosen: File) => {
  if (!validateExt(chosen.name)) {
    errorMsg.value = `Unsupported file type: ${chosen.name}`
    return
  }
  if (chosen.size > MAX_UPLOAD_BYTES) {
    errorMsg.value = 'File exceeds 850 MB'
    return
  }
  errorMsg.value = ''
  file.value = chosen
  fileSize.value = chosen.size
  stage.value = 'uploading'
  progress.value = 0
  log.value = []
  currentMsg.value = 'Uploading…'

  abortController = new AbortController()
  const form = new FormData()
  form.append('file', chosen)

  try {
    const resp = await fetch('/api/admin/books/upload', {
      method: 'POST',
      body: form,
      credentials: 'include',
      signal: abortController.signal,
    })
    if (!resp.ok || !resp.body) {
      const text = await resp.text().catch(() => '')
      throw new Error(text || `HTTP ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        handleSseEvent(raw)
      }
    }
  } catch (err: any) {
    if (err?.name !== 'AbortError') {
      errorMsg.value = err?.message || 'Upload failed'
      stage.value = 'idle'
    }
  } finally {
    abortController = null
  }
}

const handleSseEvent = (raw: string) => {
  let eventName = 'message'
  let dataStr = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim()
    else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
  }
  if (!dataStr) return
  let payload: any
  try { payload = JSON.parse(dataStr) } catch { return }

  if (eventName === 'log') {
    log.value.push(payload as LogEntry)
    currentMsg.value = (payload as LogEntry).msg || currentMsg.value
    progress.value = Math.min(95, progress.value + 6)
  } else if (eventName === 'done') {
    progress.value = 100
    if (payload.existing) {
      existingBook.value = payload.existing
      stage.value = 'duplicate'
    } else if (payload.error) {
      errorMsg.value = payload.error
      stage.value = 'idle'
    } else {
      stagingId.value = payload.staging_id
      fileHash.value = payload.hash
      fileFormat.value = payload.format
      fileSize.value = payload.size
      stagingCoverUrl.value = payload.cover_url
      meta.value = { ...DEFAULT_META, ...(payload.extracted_metadata || {}) }
      stage.value = 'review'
    }
  }
}

const onFileChosen = (e: Event) => {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length) startUpload(input.files[0])
  input.value = ''
}

const onDrop = (e: DragEvent) => {
  e.preventDefault()
  dragOver.value = false
  if (e.dataTransfer?.files?.length) startUpload(e.dataTransfer.files[0])
}

const onDragOver = (e: DragEvent) => { e.preventDefault(); dragOver.value = true }
const onDragLeave = () => { dragOver.value = false }
const openPicker = () => fileInputEl.value?.click()

const commit = async () => {
  if (!stagingId.value || titleMissing.value) return
  stage.value = 'committing'
  errorMsg.value = ''
  try {
    if (coverOverride.value) {
      const cform = new FormData()
      cform.append('file', coverOverride.value)
      await api.post(`/admin/books/upload/${stagingId.value}/cover`, cform, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    }
    const payload = {
      staging_id: stagingId.value,
      metadata: meta.value,
      top_dir: topDir.value,
      subpath: subpath.value,
      clearance: clearance.value,
      needs_review: needsReview.value,
    }
    const res = await api.post('/admin/books/commit', payload)
    committedBook.value = res.data
    stagingId.value = null
    stage.value = 'done'
  } catch (err: any) {
    errorMsg.value = err.response?.data?.detail || err.message
    stage.value = 'review'
  }
}

const onReextract = async () => {
  // For upload staging there's no source file other than the in-flight one;
  // just re-trigger the cover endpoint on the staging item by re-uploading
  // is out of scope here, so this is a no-op for the upload flow.
}

const cancelUpload = () => {
  if (abortController) abortController.abort()
  resetToIdle()
}

const stepStates = computed(() => {
  const steps: { key: string, label: string, desc: string }[] = [
    { key: 'select',  label: t('admin.upload.step.select.label'),  desc: t('admin.upload.step.select.desc') },
    { key: 'extract', label: t('admin.upload.step.extract.label'), desc: t('admin.upload.step.extract.desc') },
    { key: 'review',  label: t('admin.upload.step.review.label'),  desc: t('admin.upload.step.review.desc') },
    { key: 'commit',  label: t('admin.upload.step.commit.label'),  desc: t('admin.upload.step.commit.desc') },
  ]
  return steps.map((s, i) => ({
    ...s,
    done: i < stepIndex.value,
    active: i === stepIndex.value && stepIndex.value < 4,
  }))
})

const reviewCoverMeta = computed(() => {
  if (!stagingCoverUrl.value) return ''
  return t('admin.upload.review.cover_meta', { w: '—', h: '—' })
})
</script>

<template>
  <div class="space-y-6 max-w-5xl mx-auto px-6 py-6">
    <AdminNav />

    <div>
      <h2 class="text-xl font-semibold text-gray-900 dark:text-white">{{ t('admin.upload.title') }}</h2>
      <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ t('admin.upload.subtitle') }}</p>
    </div>

    <ol class="flex items-center gap-0 mb-2">
      <template v-for="(s, i) in stepStates" :key="s.key">
        <li class="flex items-center gap-3 shrink-0">
          <div
            class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold border-2 transition-colors"
            :class="s.done
              ? 'bg-blue-600 border-blue-600 text-white'
              : s.active
                ? 'bg-white dark:bg-gray-800 border-blue-600 text-blue-600 dark:text-blue-400 pulse-ring'
                : 'bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-600 text-gray-400'"
          >
            <CheckIcon v-if="s.done" class="w-4 h-4" />
            <span v-else>{{ i + 1 }}</span>
          </div>
          <div :class="!s.done && !s.active ? 'opacity-50' : ''">
            <div class="text-sm font-semibold text-gray-900 dark:text-white">{{ s.label }}</div>
            <div class="text-xs text-gray-500 dark:text-gray-400">{{ s.desc }}</div>
          </div>
        </li>
        <div
          v-if="i < stepStates.length - 1"
          class="flex-1 h-px mx-4"
          :class="stepStates[i].done ? 'bg-blue-600' : 'bg-gray-200 dark:bg-gray-700'"
        ></div>
      </template>
    </ol>

    <div v-if="errorMsg" class="rounded border border-red-200 dark:border-red-900/60 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 text-sm px-4 py-2">
      {{ errorMsg }}
    </div>

    <!-- ===== DROP ZONE (idle) ===== -->
    <div v-if="stage === 'idle'" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-8">
      <div
        @click="openPicker"
        @drop="onDrop"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        class="rounded-xl border-2 border-dashed p-12 text-center cursor-pointer transition-colors"
        :class="dragOver
          ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
          : 'border-gray-300 dark:border-gray-600 hover:border-blue-400 hover:bg-gray-50 dark:hover:bg-gray-700/40'"
      >
        <div class="mx-auto w-14 h-14 rounded-full bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 flex items-center justify-center">
          <ArrowUpTrayIcon class="w-7 h-7" />
        </div>
        <h3 class="mt-4 text-lg font-semibold text-gray-900 dark:text-white">{{ t('admin.upload.drop.heading') }}</h3>
        <p class="mt-1 text-sm text-gray-600 dark:text-gray-400">{{ t('admin.upload.drop.or') }}&nbsp;<span class="text-blue-600 dark:text-blue-400 underline">{{ t('admin.upload.drop.browse_link') }}</span>&nbsp;{{ t('admin.upload.drop.your_computer') }}</p>
        <div class="mt-4 inline-flex flex-wrap items-center justify-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
          <span>{{ t('admin.upload.drop.accepted') }}</span>
          <span
            v-for="ext in ACCEPTED"
            :key="ext"
            class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-xs"
          >{{ ext }}</span>
        </div>
        <div class="mt-4 text-xs text-gray-400 dark:text-gray-500">{{ t('admin.upload.drop.max_size') }}</div>
        <input
          ref="fileInputEl"
          type="file"
          :accept="ACCEPT_ATTR"
          class="hidden"
          @change="onFileChosen"
        />
      </div>

      <div class="mt-6 flex items-start gap-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-4 py-3 text-sm">
        <ExclamationTriangleIcon class="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
        <div>
          <div class="font-semibold text-amber-800 dark:text-amber-200">
            {{ t('admin.upload.clearance_warning.title_prefix') }}
            <span class="font-mono">100</span>
            {{ t('admin.upload.clearance_warning.title_suffix') }}
          </div>
          <div class="text-xs text-amber-700 dark:text-amber-300 mt-0.5">{{ t('admin.upload.clearance_warning.body') }}</div>
        </div>
      </div>
    </div>

    <!-- ===== PROCESSING (uploading + duplicate) ===== -->
    <div v-if="stage === 'uploading' || stage === 'duplicate'" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700">
      <div class="p-6 flex items-center gap-4">
        <div
          class="w-12 h-12 rounded-lg flex items-center justify-center shrink-0"
          :class="stage === 'duplicate'
            ? 'bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400'
            : 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'"
        >
          <ExclamationTriangleIcon v-if="stage === 'duplicate'" class="w-6 h-6" />
          <ArrowPathIcon v-else class="w-6 h-6 animate-spin" />
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-baseline justify-between gap-3">
            <div class="text-sm font-semibold text-gray-900 dark:text-white truncate">{{ file?.name || '—' }}</div>
            <div class="font-mono text-xs text-gray-500 dark:text-gray-400 shrink-0">{{ fmtBytes(fileSize) }}</div>
          </div>
          <div class="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden mt-2">
            <div
              class="h-full transition-all duration-300"
              :class="stage === 'duplicate' ? 'bg-red-500' : 'bg-blue-500'"
              :style="{ width: progress + '%' }"
            ></div>
          </div>
          <div class="mt-1 flex items-center justify-between text-xs">
            <span class="text-gray-600 dark:text-gray-300 truncate">{{ currentMsg }}</span>
            <span class="font-mono text-gray-400 dark:text-gray-500 shrink-0 ml-2">{{ progress }}%</span>
          </div>
        </div>
        <button
          v-if="stage !== 'duplicate'"
          @click="cancelUpload"
          class="text-xs px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:border-red-300 hover:text-red-600 shrink-0"
        >{{ t('admin.upload.review.cancel') }}</button>
      </div>

      <div class="border-t border-gray-100 dark:border-gray-700">
        <button
          type="button"
          @click="logExpanded = !logExpanded"
          class="w-full flex items-center justify-between bg-gray-50 dark:bg-gray-900/40 px-4 py-2 text-xs font-medium text-gray-700 dark:text-gray-300"
        >
          <span class="inline-flex items-center gap-2">
            <ChevronDownIcon v-if="logExpanded" class="w-4 h-4" />
            <ChevronRightIcon v-else class="w-4 h-4" />
            {{ t('admin.upload.log.heading') }}
            <span class="text-gray-400">·</span>
            <span>{{ log.length === 0 ? t('admin.upload.log.entries_zero') : log.length === 1 ? t('admin.upload.log.entries_one') : t('admin.upload.log.entries_other', { count: log.length }) }}</span>
          </span>
          <span class="text-[10px] uppercase tracking-wider text-blue-500">[{{ t('admin.upload.log.live') }}]</span>
        </button>
        <div
          v-show="logExpanded"
          ref="logEl"
          class="bg-gray-900 dark:bg-black text-gray-200 font-mono text-xs p-3 max-h-56 overflow-y-auto"
        >
          <div v-for="(entry, i) in log" :key="i" class="log-line whitespace-pre-wrap break-all">
            <span class="text-gray-500">{{ entry.time }}</span>
            <span
              class="ml-2 inline-block w-12"
              :class="{
                'text-blue-300': entry.level === 'info',
                'text-emerald-400': entry.level === 'ok',
                'text-amber-400': entry.level === 'warn',
                'text-red-400': entry.level === 'error',
              }"
            >[{{ entry.level }}]</span>
            <span class="ml-2 text-gray-200">{{ entry.msg }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== DUPLICATE PANEL ===== -->
    <div v-if="stage === 'duplicate' && existingBook" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-red-200 dark:border-red-900/60 overflow-hidden">
      <div class="bg-red-50 dark:bg-red-900/20 px-6 py-4 flex items-start gap-3">
        <ExclamationTriangleIcon class="w-5 h-5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
        <div>
          <div class="font-semibold text-red-800 dark:text-red-200">{{ t('admin.upload.duplicate.heading') }}</div>
          <div class="font-mono text-xs text-red-700 dark:text-red-300 break-all mt-0.5">{{ existingBook.id }}</div>
        </div>
      </div>
      <div class="p-6">
        <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-3">{{ t('admin.upload.duplicate.existing') }}</div>
        <div class="flex gap-4">
          <CoverPreview
            :image-url="existingBook.cover_url"
            size="small"
            :readonly="true"
          />
          <div class="flex-1 min-w-0 space-y-2">
            <div class="text-base font-semibold text-gray-900 dark:text-white">{{ existingBook.title || '(no title)' }}</div>
            <div v-if="existingBook.author" class="italic text-sm text-gray-600 dark:text-gray-400">{{ existingBook.author }}</div>
            <div class="text-xs">
              <span class="text-gray-500 dark:text-gray-400 mr-1">{{ t('admin.upload.duplicate.path_label') }}</span>
              <code v-for="loc in existingBook.locations" :key="loc" class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-[11px] mr-1 break-all">/{{ loc }}</code>
            </div>
            <div class="text-xs flex items-center gap-2">
              <span class="text-gray-500 dark:text-gray-400">{{ t('admin.upload.duplicate.clearance_label') }}</span>
              <ClearancePill :value="existingBook.clearance" />
            </div>
            <div class="flex gap-2 mt-3">
              <a
                v-if="existingBook.locations[0]"
                :href="`/library/#/item/${existingBook.locations[0]}`"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-blue-600 text-white text-sm hover:bg-blue-700"
              >
                <ArrowTopRightOnSquareIcon class="w-4 h-4" />
                {{ t('admin.upload.duplicate.open') }}
              </a>
              <router-link
                :to="`/admin/books?hash=${existingBook.id}`"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
              >{{ t('admin.upload.duplicate.edit') }}</router-link>
            </div>
          </div>
        </div>
      </div>
      <div class="bg-gray-50 dark:bg-gray-900/40 px-6 py-4 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between text-xs">
        <div class="text-gray-500 dark:text-gray-400">{{ t('admin.upload.duplicate.override_hint') }}</div>
        <div class="flex gap-2">
          <button
            @click="resetToIdle"
            class="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
          >{{ t('admin.upload.duplicate.cancel') }}</button>
        </div>
      </div>
    </div>

    <!-- ===== REVIEW PANEL ===== -->
    <div v-if="stage === 'review' || stage === 'committing'" class="space-y-4">
      <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
        <div class="px-6 py-3 bg-emerald-50 dark:bg-emerald-900/20 border-b border-emerald-200 dark:border-emerald-900/60 flex items-center gap-3">
          <div class="w-7 h-7 rounded-full bg-emerald-500 text-white flex items-center justify-center">
            <CheckIcon class="w-4 h-4" />
          </div>
          <div class="flex-1 min-w-0">
            <div class="text-sm font-semibold text-emerald-800 dark:text-emerald-200">{{ t('admin.upload.review.extracted') }}</div>
            <div class="font-mono text-xs text-emerald-700 dark:text-emerald-300 truncate">
              {{ file?.name }} · {{ fileFormat.toUpperCase() }} · {{ fmtBytes(fileSize) }} · {{ fileHash?.slice(0, 16) }}…
            </div>
          </div>
          <button
            @click="showLogModal = true"
            class="text-xs text-emerald-700 dark:text-emerald-300 underline hover:no-underline"
          >{{ t('admin.upload.log.view') }}</button>
        </div>

        <div class="p-6 grid grid-cols-[200px_1fr] gap-6">
          <div>
            <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.cover') }}</div>
            <CoverPreview
              :image-url="stagingCoverUrl"
              :file="coverOverride"
              size="full"
              :meta="reviewCoverMeta"
              @update:file="(f) => coverOverride = f"
              @re-extract="onReextract"
            />
          </div>
          <div>
            <MetadataFields v-model="meta" :show-title-error="titleMissing" />
          </div>
        </div>
      </div>

      <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6 space-y-5">
        <div>
          <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.destination') }}</div>
          <DestinationPicker
            v-model:top-dir="topDir"
            v-model:subpath="subpath"
            :filename="file?.name || ''"
            :tops="topDirs"
          />
        </div>
        <div class="border-t border-gray-100 dark:border-gray-700 pt-5">
          <div class="text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2">{{ t('admin.upload.review.access') }}</div>
          <div class="grid grid-cols-2 gap-6">
            <div>
              <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{ t('admin.upload.review.clearance') }}</label>
              <ClearanceControl v-model="clearance" />
            </div>
            <div>
              <label class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input v-model="needsReview" type="checkbox" class="rounded" />
                <span v-html="t('admin.upload.review.needs_review', { flag: '<code class=\'font-mono text-xs\'>needs_review</code>' })"></span>
              </label>
              <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ t('admin.upload.review.needs_review_help') }}</p>
            </div>
          </div>
        </div>
      </div>

      <div class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-4 flex items-center justify-between">
        <button
          @click="resetToIdle"
          :disabled="stage === 'committing'"
          class="px-4 py-2 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
        >{{ t('admin.upload.review.cancel') }}</button>
        <div class="flex items-center gap-3">
          <div v-if="titleMissing" class="inline-flex items-center gap-1.5 text-xs text-red-600 dark:text-red-400">
            <ExclamationTriangleIcon class="w-4 h-4" />
            {{ t('admin.upload.review.title_required') }}
          </div>
          <button
            @click="commit"
            :disabled="titleMissing || stage === 'committing'"
            class="inline-flex items-center gap-2 px-4 py-2 rounded bg-blue-600 text-white text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            <ArrowPathIcon v-if="stage === 'committing'" class="w-4 h-4 animate-spin" />
            <span>{{ stage === 'committing' ? t('admin.upload.review.committing') : t('admin.upload.review.commit') }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- ===== SUCCESS PANEL ===== -->
    <div v-if="stage === 'done' && committedBook" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-emerald-200 dark:border-emerald-900/60 overflow-hidden">
      <div class="bg-gradient-to-b from-emerald-50 to-white dark:from-emerald-900/20 dark:to-gray-800 px-6 py-8 text-center">
        <div class="mx-auto w-14 h-14 rounded-full bg-emerald-500 text-white flex items-center justify-center">
          <CheckIcon class="w-8 h-8" />
        </div>
        <div class="mt-3 text-xl font-bold text-gray-900 dark:text-white">{{ t('admin.upload.success.heading') }}</div>
        <div class="text-sm text-gray-600 dark:text-gray-400 mt-1">{{ t('admin.upload.success.subtitle') }}</div>
      </div>
      <div class="p-6 flex gap-4">
        <CoverPreview
          :image-url="committedBook.cover_url"
          size="small"
          :readonly="true"
        />
        <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs flex-1 min-w-0">
          <dt class="text-gray-500 dark:text-gray-400">{{ t('admin.upload.success.path') }}</dt>
          <dd class="font-mono text-gray-800 dark:text-gray-200 break-all">/{{ committedBook.locations[0] }}</dd>
          <dt class="text-gray-500 dark:text-gray-400">{{ t('admin.upload.success.hash') }}</dt>
          <dd class="font-mono text-gray-800 dark:text-gray-200 break-all">{{ committedBook.id }}</dd>
          <dt class="text-gray-500 dark:text-gray-400">{{ t('admin.upload.success.clearance') }}</dt>
          <dd>
            <ClearancePill :value="committedBook.clearance" />
          </dd>
        </dl>
      </div>
      <div class="px-6 py-4 bg-gray-50 dark:bg-gray-900/40 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between">
        <a
          v-if="committedBook.locations[0]"
          :href="`/library/#/item/${committedBook.locations[0]}`"
          target="_blank"
          rel="noopener"
          class="inline-flex items-center gap-1.5 text-sm text-blue-600 dark:text-blue-400 hover:underline"
        >
          <ArrowTopRightOnSquareIcon class="w-4 h-4" />
          {{ t('admin.upload.success.view') }}
        </a>
        <div class="flex gap-2">
          <router-link
            :to="`/admin/books?hash=${committedBook.id}`"
            class="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
          >{{ t('admin.upload.success.edit') }}</router-link>
          <button
            @click="resetToIdle"
            class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-blue-600 text-white text-sm hover:bg-blue-700"
          >
            <ArrowUpTrayIcon class="w-4 h-4" />
            {{ t('admin.upload.success.upload_another') }}
          </button>
        </div>
      </div>
    </div>

    <!-- ===== LOG MODAL ===== -->
    <div
      v-if="showLogModal"
      class="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6"
      @click.self="showLogModal = false"
    >
      <div class="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col">
        <div class="flex items-center justify-between p-4 border-b border-gray-100 dark:border-gray-700">
          <h3 class="text-sm font-semibold text-gray-900 dark:text-white">{{ t('admin.upload.log.heading') }}</h3>
          <button @click="showLogModal = false" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <XMarkIcon class="w-5 h-5" />
          </button>
        </div>
        <div class="flex-1 overflow-y-auto bg-gray-900 dark:bg-black text-gray-200 font-mono text-xs p-3">
          <div v-for="(entry, i) in log" :key="i" class="log-line whitespace-pre-wrap break-all">
            <span class="text-gray-500">{{ entry.time }}</span>
            <span
              class="ml-2 inline-block w-12"
              :class="{
                'text-blue-300': entry.level === 'info',
                'text-emerald-400': entry.level === 'ok',
                'text-amber-400': entry.level === 'warn',
                'text-red-400': entry.level === 'error',
              }"
            >[{{ entry.level }}]</span>
            <span class="ml-2 text-gray-200">{{ entry.msg }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
@keyframes pulse-ring {
  0%   { box-shadow: 0 0 0 0 rgba(59,130,246,0.45); }
  70%  { box-shadow: 0 0 0 12px rgba(59,130,246,0); }
  100% { box-shadow: 0 0 0 0 rgba(59,130,246,0); }
}
.pulse-ring { animation: pulse-ring 1.6s infinite; }

@keyframes log-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}
.log-line { animation: log-in 200ms ease-out both; }
</style>
