<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ArrowUpTrayIcon,
  CheckIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  ArrowTopRightOnSquareIcon,
  XMarkIcon,
  PlusIcon,
  TrashIcon,
} from '@heroicons/vue/24/outline'
import api from '../api'
import AdminNav from '../components/AdminNav.vue'
import ClearancePill from '../components/ClearancePill.vue'
import CoverPreview from '../components/CoverPreview.vue'
import UploadItemEditor from '../components/upload/UploadItemEditor.vue'
import {
  DEFAULT_META, COPYABLE_META_KEYS, sourceName, makeItem,
  type UploadItem, type UploadSource, type UploadStatus,
} from '../components/upload/uploadTypes'
import { detectVolume, sameSet, naturalCompare, incrementTitle } from '../lib/volume'
import { pendingServerImports } from '../lib/pendingImports'

const { t } = useI18n({ useScope: 'global' })

const ACCEPTED = ['FB2', 'FB2.ZIP', 'EPUB', 'PDF', 'DJVU', 'MOBI', 'AZW', 'AZW3', 'PRC', 'DOCX', 'ODT', 'RTF', 'HTML', 'TXT', 'TXT.ZIP', 'MD.ZIP', 'MARKDOWN.ZIP', 'JPG', 'JPEG', 'MP3', 'WAV', 'OGG', 'FLAC', 'M4A', 'AAC', 'MP4', 'WebM', 'MKV', 'AVI', 'MOV']
const ACCEPT_ATTR = '.fb2,.zip,.epub,.pdf,.djvu,.mobi,.azw,.azw3,.prc,.docx,.odt,.rtf,.html,.txt,.jpg,.jpeg,.mp3,.wav,.ogg,.flac,.m4a,.aac,.mp4,.webm,.mkv,.avi,.mov'
const MAX_UPLOAD_BYTES = 850 * 1024 * 1024
const MAX_BATCH = 40

const items = ref<UploadItem[]>([])
const activeId = ref<string | null>(null)
const dragOver = ref(false)
const errorMsg = ref('')
const batchSummary = ref<{ done: number, skipped: number, failed: number } | null>(null)
const committingAll = ref(false)

const showLogModal = ref(false)
const logModalId = ref<string | null>(null)
const fileInputEl = ref<HTMLInputElement | null>(null)

// non-reactive: in-flight upload aborters keyed by item.localId
const aborters = new Map<string, AbortController>()
let keepalive: number | undefined
let batchSeq = 0   // monotonic suffix for audit batch ids (no crypto.randomUUID on plain http)

// Admin access is enforced by the router's /admin/* beforeEach guard, so by the
// time this view mounts the visitor is a confirmed admin — just initialise.
onMounted(() => {
  // Pick up files handed over from the Browse "Import to library" action.
  if (pendingServerImports.value.length) {
    const paths = pendingServerImports.value
    pendingServerImports.value = []
    addSources(paths.map((p) => ({ kind: 'server' as const, path: p })))
  }
  // Keepalive: while the page holds staged (uncommitted) files, refresh their
  // 1-hour server-side TTL so a long editing session never expires. Interval is
  // well under the TTL; closing the tab stops it so abandoned uploads still
  // expire and get swept.
  keepalive = window.setInterval(() => {
    // Only refresh the ids THIS page holds — not all of the admin's staging —
    // so one open tab can't keep unrelated abandoned uploads alive elsewhere.
    const ids = items.value
      .filter((i) => i.stagingId && (i.status === 'staged' || i.status === 'committing'))
      .map((i) => i.stagingId as string)
    if (ids.length) api.post('/admin/books/upload/touch', { staging_ids: ids }).catch(() => {})
  }, 5 * 60 * 1000)
})

// A staging dir is safe to cancel only when it exists and isn't committed or
// mid-commit — never DELETE under an in-flight commit (the backend is still
// reading rec["dir"]).
const cancellable = (it: UploadItem) =>
  !!it.stagingId && it.status !== 'committed' && it.status !== 'committing'

onBeforeUnmount(() => {
  if (keepalive !== undefined) clearInterval(keepalive)
  for (const ac of aborters.values()) ac.abort()
  // A batch commit keeps running after an SPA route change; deleting its staged
  // items would 410 it. Leave everything — the TTL/sweep reaps any leftovers.
  if (committingAll.value) return
  for (const it of items.value) {
    if (cancellable(it)) api.delete(`/admin/books/upload/${it.stagingId}`).catch(() => {})
  }
})

// ---- derived ---------------------------------------------------------------

const sortedItems = computed(() =>
  [...items.value].sort((a, b) => naturalCompare(sourceName(a.source), sourceName(b.source))))

const activeItem = computed<UploadItem | null>(() =>
  items.value.find((i) => i.localId === activeId.value) || null)

// First usable item (its metadata seeds prefill for the rest).
const templateItem = computed<UploadItem | null>(() =>
  sortedItems.value.find((i) => i.status === 'staged' || i.status === 'committing' || i.status === 'committed') || null)

const committableCount = computed(() =>
  items.value.filter((i) => i.status === 'staged' && (i.meta.title || '').trim()).length)

const logModalItem = computed(() => items.value.find((i) => i.localId === logModalId.value) || null)

const stepIndex = computed(() => {
  if (!items.value.length) return 0
  if (items.value.some((i) => i.status === 'queued' || i.status === 'uploading')) return 1
  if (items.value.every((i) => i.status === 'committed')) return 4
  if (items.value.some((i) => i.status === 'committing')) return 3
  return 2
})

const stepStates = computed(() => {
  const steps = [
    { key: 'select', label: t('admin.upload.step.select.label'), desc: t('admin.upload.step.select.desc') },
    { key: 'extract', label: t('admin.upload.step.extract.label'), desc: t('admin.upload.step.extract.desc') },
    { key: 'review', label: t('admin.upload.step.review.label'), desc: t('admin.upload.step.review.desc') },
    { key: 'commit', label: t('admin.upload.step.commit.label'), desc: t('admin.upload.step.commit.desc') },
  ]
  return steps.map((s, i) => ({ ...s, done: i < stepIndex.value, active: i === stepIndex.value && stepIndex.value < 4 }))
})

const fmtBytes = (n: number) => {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB']
  let f = n / 1024, i = 0
  while (f >= 1024 && i < units.length - 1) { f /= 1024; i++ }
  return `${f.toFixed(1)} ${units[i]}`
}

const lastMsg = (item: UploadItem) =>
  item.log.length ? item.log[item.log.length - 1].msg : t('admin.upload.batch.uploading')

const titleMissing = (item: UploadItem) => !(item.meta.title || '').trim()

const destEmpty = (item: UploadItem) => {
  const a = (item.selectedDir || '').replace(/^\/+|\/+$/g, '')
  const b = (item.extraSubpath || '').replace(/^\/+|\/+$/g, '')
  return !a && !b
}

const statusMeta = (s: UploadStatus): { key: string, dot: string } => {
  switch (s) {
    case 'queued': return { key: 'queued', dot: 'bg-gray-400' }
    case 'uploading': return { key: 'uploading', dot: 'bg-blue-500 animate-pulse' }
    case 'staged': return { key: 'staged', dot: 'bg-blue-500' }
    case 'committing': return { key: 'committing', dot: 'bg-blue-500 animate-pulse' }
    case 'committed': return { key: 'committed', dot: 'bg-emerald-500' }
    case 'duplicate': return { key: 'duplicate', dot: 'bg-amber-500' }
    case 'error': return { key: 'error', dot: 'bg-red-500' }
  }
}

// ---- selection -------------------------------------------------------------

const validateExt = (name: string): boolean => {
  const lower = name.toLowerCase()
  if (lower.endsWith('.fb2.zip')) return true
  return ACCEPT_ATTR.split(',').some((ext) => lower.endsWith(ext))
}

const filesToSources = (files: FileList | File[]): UploadSource[] =>
  Array.from(files).map((f) => ({ kind: 'local' as const, file: f }))

const addSources = (sources: UploadSource[]) => {
  errorMsg.value = ''
  batchSummary.value = null
  const skipped: string[] = []
  let capped = 0
  for (const src of sources) {
    if (items.value.length >= MAX_BATCH) { capped++; continue }
    const name = sourceName(src)
    if (!validateExt(name)) { skipped.push(name); continue }
    if (src.kind === 'local' && src.file.size > MAX_UPLOAD_BYTES) { skipped.push(`${name} (>850MB)`); continue }
    items.value.push(makeItem(src))
  }
  const notices: string[] = []
  if (skipped.length) notices.push(t('admin.upload.batch.skipped', { files: skipped.join(', ') }))
  if (capped) notices.push(t('admin.upload.batch.cap', { max: MAX_BATCH }))
  errorMsg.value = notices.join(' · ')
  if (!activeId.value && sortedItems.value.length) activeId.value = sortedItems.value[0].localId
  void processQueue()
}

const onFileChosen = (e: Event) => {
  const input = e.target as HTMLInputElement
  if (input.files?.length) addSources(filesToSources(input.files))
  input.value = ''
}
const onDrop = (e: DragEvent) => {
  e.preventDefault()
  dragOver.value = false
  if (e.dataTransfer?.files?.length) addSources(filesToSources(e.dataTransfer.files))
}
const onDragOver = (e: DragEvent) => { e.preventDefault(); dragOver.value = true }
const onDragLeave = () => { dragOver.value = false }
const openPicker = () => fileInputEl.value?.click()

// ---- staging (sequential) --------------------------------------------------

let processing = false
const processQueue = async () => {
  if (processing) return
  processing = true
  try {
    while (true) {
      const next = sortedItems.value.find((i) => i.status === 'queued')
      if (!next) break
      await stageItem(next)
    }
  } finally {
    processing = false
  }
}

const stageItem = async (item: UploadItem) => {
  item.status = 'uploading'
  item.progress = 0
  item.log = []
  item.errorMsg = ''

  // Server-side import: the file is already on the server (e.g. /Books/Unsorted).
  // The backend stages it and returns the same payload the multipart upload does.
  if (item.source.kind === 'server') {
    item.progress = 50
    try {
      const res = await api.post('/admin/books/stage-from-path', { path: item.source.path })
      applyStaged(item, res.data)
    } catch (err: any) {
      item.errorMsg = err.response?.data?.detail || err.message || 'Import failed'
      item.status = 'error'
    }
    return
  }

  const ac = new AbortController()
  aborters.set(item.localId, ac)
  const form = new FormData()
  form.append('file', item.source.file)
  try {
    const resp = await fetch(`${api.defaults.baseURL ?? '/api'}/admin/books/upload`, {
      method: 'POST', body: form, credentials: 'include', signal: ac.signal,
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
        handleSse(item, raw)
      }
    }
  } catch (err: any) {
    if (err?.name !== 'AbortError') {
      item.errorMsg = err?.message || 'Upload failed'
      item.status = 'error'
    }
  } finally {
    aborters.delete(item.localId)
  }
}

// Apply a staging-result payload to an item. Shared by the multipart SSE `done`
// event and the server-import JSON response.
const applyStaged = (item: UploadItem, payload: any) => {
  item.progress = 100
  if (payload.existing) {
    item.existingBook = payload.existing
    item.status = 'duplicate'
  } else if (payload.error) {
    item.errorMsg = payload.error
    item.status = 'error'
  } else {
    item.stagingId = payload.staging_id
    item.hash = payload.hash
    item.format = payload.format
    item.size = payload.size
    item.stagingFilename = payload.filename || sourceName(item.source)
    item.filename = item.stagingFilename
    item.stagingCoverUrl = payload.cover_url
    item.meta = { ...DEFAULT_META, ...(payload.extracted_metadata || {}) }
    item.status = 'staged'
    prefillItem(item)
  }
}

const handleSse = (item: UploadItem, raw: string) => {
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
    item.log.push(payload)
    item.progress = Math.min(95, item.progress + 6)
  } else if (eventName === 'done') {
    applyStaged(item, payload)
  }
}

// ---- prefill ---------------------------------------------------------------

const copyWorkflow = (from: UploadItem, to: UploadItem) => {
  to.selectedDir = from.selectedDir
  to.extraSubpath = from.extraSubpath
  to.clearance = from.clearance
  to.needsReview = from.needsReview
}

// Volume-incremented title from the template when both filenames carry a number,
// else the template title verbatim. `to` keeps its own title only when `force`
// is off and no increment is possible.
const applyTitle = (from: UploadItem, to: UploadItem, force: boolean) => {
  const tmplTitle = (from.meta.title || '').trim()
  if (!tmplTitle) return
  const fv = detectVolume(sourceName(from.source))
  const tv = detectVolume(sourceName(to.source))
  if (fv && tv) to.meta.title = incrementTitle(from.meta.title as string, fv.num, tv.num)
  else if (force) to.meta.title = from.meta.title
}

// Auto-prefill (silent, at stage time): only copy metadata to true set members.
const copyMetaIfSet = (from: UploadItem, to: UploadItem) => {
  if (!sameSet(sourceName(from.source), sourceName(to.source))) return
  for (const k of COPYABLE_META_KEYS) to.meta[k] = from.meta[k]
  applyTitle(from, to, false)
}

// Manual "Apply first to all": force every filled field onto the target,
// regardless of whether its filename fits the series.
const forceCopyMeta = (from: UploadItem, to: UploadItem) => {
  for (const k of COPYABLE_META_KEYS) to.meta[k] = from.meta[k]
  applyTitle(from, to, true)
}

// Runs once when an item is freshly staged.
const prefillItem = (item: UploadItem) => {
  if (item.prefilled) return
  const tmpl = templateItem.value
  if (tmpl && tmpl.localId !== item.localId) {
    copyWorkflow(tmpl, item)
    copyMetaIfSet(tmpl, item)
  }
  item.prefilled = true
}

// Manual re-propagation from the first item after the admin edits it. Forces all
// filled fields onto every editable item except those ticked "exclude".
const applyTemplateToAll = () => {
  const tmpl = templateItem.value
  if (!tmpl) return
  for (const it of items.value) {
    if (it.localId === tmpl.localId) continue
    if (it.status !== 'staged') continue
    if (it.excludeFromApply) continue
    copyWorkflow(tmpl, it)
    forceCopyMeta(tmpl, it)
  }
}

// ---- commit ----------------------------------------------------------------

const commitItem = async (item: UploadItem, batchId?: string): Promise<boolean> => {
  if (item.status !== 'staged') return false
  if (titleMissing(item)) { item.errorMsg = t('admin.upload.review.title_required'); return false }
  if (!item.stagingId) { item.errorMsg = t('admin.upload.batch.expired'); item.status = 'error'; return false }
  item.status = 'committing'
  item.errorMsg = ''
  try {
    if (item.coverOverride) {
      const cform = new FormData()
      cform.append('file', item.coverOverride)
      await api.post(`/admin/books/upload/${item.stagingId}/cover`, cform, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    }
    const combinedSubpath = [item.selectedDir, item.extraSubpath]
      .map((s) => (s || '').replace(/^\/+|\/+$/g, ''))
      .filter(Boolean)
      .join('/')
    const res = await api.post('/admin/books/commit', {
      staging_id: item.stagingId,
      metadata: item.meta,
      top_dir: '',
      subpath: combinedSubpath,
      clearance: item.clearance,
      needs_review: item.needsReview,
      filename: item.filename,
      ...(batchId ? { batch_id: batchId } : {}),
    })
    item.committedBook = res.data
    item.stagingId = null
    item.status = 'committed'
    return true
  } catch (err: any) {
    item.errorMsg = err.response?.data?.detail || err.message
    item.status = 'staged'
    return false
  }
}

const onItemCommit = (item: UploadItem) => {
  if (destEmpty(item) && !window.confirm(t('admin.upload.review.destination_empty.confirm'))) return
  void commitItem(item)
}

const commitAll = async () => {
  if (committingAll.value) return
  const targets = sortedItems.value.filter((i) => i.status === 'staged' && (i.meta.title || '').trim())
  if (!targets.length) { errorMsg.value = t('admin.upload.batch.nothing'); return }
  const invalid = items.value.filter((i) => i.status === 'staged' && !(i.meta.title || '').trim()).length
  const dups = items.value.filter((i) => i.status === 'duplicate').length
  if (targets.some(destEmpty) && !window.confirm(t('admin.upload.review.destination_empty.confirm'))) return
  errorMsg.value = ''
  committingAll.value = true
  // ≥2 books → fold them into a single audit-log entry via a shared batch id.
  const batchId = targets.length >= 2 ? `b${Date.now().toString(36)}-${(batchSeq++).toString(36)}` : undefined
  let done = 0, failed = 0
  for (const it of targets) {
    const ok = await commitItem(it, batchId)
    if (ok) done++; else failed++
  }
  committingAll.value = false
  batchSummary.value = { done, skipped: invalid + dups, failed }
  const next = sortedItems.value.find((i) => i.status !== 'committed')
  if (next) activeId.value = next.localId
}

// ---- item lifecycle --------------------------------------------------------

const setActive = (item: UploadItem) => { activeId.value = item.localId }

const removeItem = (item: UploadItem) => {
  if (item.status === 'committing') return  // don't yank staging from under an in-flight commit
  const ac = aborters.get(item.localId)
  if (ac) { ac.abort(); aborters.delete(item.localId) }
  if (cancellable(item)) {
    api.delete(`/admin/books/upload/${item.stagingId}`).catch(() => {})
  }
  const idx = items.value.findIndex((i) => i.localId === item.localId)
  if (idx >= 0) items.value.splice(idx, 1)
  if (activeId.value === item.localId) activeId.value = sortedItems.value[0]?.localId ?? null
  if (!items.value.length) { errorMsg.value = ''; batchSummary.value = null }
}

const restageItem = (item: UploadItem) => {
  item.status = 'queued'
  item.errorMsg = ''
  item.progress = 0
  item.prefilled = false
  item.existingBook = null
  void processQueue()
}

const viewLog = (item: UploadItem) => { logModalId.value = item.localId; showLogModal.value = true }
</script>

<template>
  <div class="space-y-6">
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

    <div v-if="batchSummary" class="rounded border border-blue-200 dark:border-blue-900/60 bg-blue-50 dark:bg-blue-900/20 text-blue-800 dark:text-blue-200 text-sm px-4 py-2">
      {{ t('admin.upload.batch.summary', { done: batchSummary.done, skipped: batchSummary.skipped, failed: batchSummary.failed }) }}
    </div>

    <!-- ===== DROP ZONE (no items) ===== -->
    <div v-if="items.length === 0" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-8">
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
        <div class="mt-2 text-xs text-gray-500 dark:text-gray-400">{{ t('admin.upload.batch.multi_hint') }}</div>
        <div class="mt-4 inline-flex flex-wrap items-center justify-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
          <span>{{ t('admin.upload.drop.accepted') }}</span>
          <span v-for="ext in ACCEPTED" :key="ext" class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-xs">{{ ext }}</span>
        </div>
        <div class="mt-4 text-xs text-gray-400 dark:text-gray-500">{{ t('admin.upload.drop.max_size') }}</div>
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

    <!-- ===== WORKSPACE (has items) ===== -->
    <div v-else class="flex gap-4 items-start">
      <!-- sidebar list (only when multiple) -->
      <aside v-if="items.length > 1" class="w-64 shrink-0 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 flex flex-col">
        <div class="px-3 py-2 border-b border-gray-100 dark:border-gray-700 text-xs uppercase tracking-wider text-gray-500 dark:text-gray-400">
          {{ t('admin.upload.batch.files_count', { count: items.length }) }}
        </div>
        <div class="flex-1 overflow-y-auto max-h-[60vh] p-1">
          <div
            v-for="it in sortedItems"
            :key="it.localId"
            @click="setActive(it)"
            role="button"
            class="group w-full flex items-center gap-2 px-2 py-2 rounded text-left cursor-pointer"
            :class="it.localId === activeId ? 'bg-blue-50 dark:bg-blue-900/30' : 'hover:bg-gray-50 dark:hover:bg-gray-700/40'"
          >
            <span class="w-2 h-2 rounded-full shrink-0" :class="statusMeta(it.status).dot"></span>
            <span class="flex-1 min-w-0">
              <span class="block text-sm text-gray-800 dark:text-gray-100 truncate font-mono">{{ sourceName(it.source) }}</span>
              <span class="block text-[11px] text-gray-500 dark:text-gray-400">
                {{ t('admin.upload.batch.status.' + statusMeta(it.status).key) }}
                <span v-if="it.status === 'staged' && titleMissing(it)" class="text-red-500"> · {{ t('admin.upload.batch.no_title') }}</span>
              </span>
            </span>
            <input
              v-if="templateItem && it.localId !== templateItem.localId"
              type="checkbox"
              v-model="it.excludeFromApply"
              @click.stop
              class="shrink-0 rounded border-gray-300 dark:border-gray-600"
              :title="t('admin.upload.batch.exclude_hint')"
            />
            <button
              v-if="it.status !== 'committing'"
              @click.stop="removeItem(it)"
              class="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 shrink-0"
              :title="t('admin.upload.batch.remove')"
            >
              <XMarkIcon class="w-4 h-4" />
            </button>
          </div>
        </div>
        <div class="p-2 border-t border-gray-100 dark:border-gray-700 space-y-2">
          <button
            @click="openPicker"
            class="w-full inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <PlusIcon class="w-4 h-4" /> {{ t('admin.upload.batch.add_files') }}
          </button>
          <button
            @click="applyTemplateToAll"
            :disabled="!templateItem"
            class="w-full inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >{{ t('admin.upload.batch.apply_all') }}</button>
          <button
            @click="commitAll"
            :disabled="committingAll || committableCount === 0"
            class="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded bg-blue-600 text-white text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            <ArrowPathIcon v-if="committingAll" class="w-4 h-4 animate-spin" />
            {{ t('admin.upload.batch.commit_all', { count: committableCount }) }}
          </button>
        </div>
      </aside>

      <!-- main pane: the active item -->
      <main class="flex-1 min-w-0">
        <div v-if="items.length === 1" class="mb-3 flex justify-end">
          <button
            @click="openPicker"
            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <PlusIcon class="w-4 h-4" /> {{ t('admin.upload.batch.add_files') }}
          </button>
        </div>

        <template v-if="activeItem">
          <!-- uploading / queued -->
          <div
            v-if="activeItem.status === 'uploading' || activeItem.status === 'queued'"
            class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700"
          >
            <div class="p-6 flex items-center gap-4">
              <div class="w-12 h-12 rounded-lg flex items-center justify-center shrink-0 bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400">
                <ArrowPathIcon class="w-6 h-6 animate-spin" />
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-baseline justify-between gap-3">
                  <div class="text-sm font-semibold text-gray-900 dark:text-white truncate">{{ sourceName(activeItem.source) }}</div>
                  <div class="font-mono text-xs text-gray-500 dark:text-gray-400 shrink-0">{{ fmtBytes(activeItem.size) }}</div>
                </div>
                <div class="h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden mt-2">
                  <div class="h-full bg-blue-500 transition-all duration-300" :style="{ width: activeItem.progress + '%' }"></div>
                </div>
                <div class="mt-1 flex items-center justify-between text-xs">
                  <span class="text-gray-600 dark:text-gray-300 truncate">{{ lastMsg(activeItem) }}</span>
                  <span class="font-mono text-gray-400 dark:text-gray-500 shrink-0 ml-2">{{ activeItem.progress }}%</span>
                </div>
              </div>
              <button
                @click="removeItem(activeItem)"
                class="text-xs px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:border-red-300 hover:text-red-600 shrink-0"
              >{{ t('admin.upload.review.cancel') }}</button>
            </div>
          </div>

          <!-- duplicate -->
          <div
            v-else-if="activeItem.status === 'duplicate' && activeItem.existingBook"
            class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-amber-200 dark:border-amber-900/60 overflow-hidden"
          >
            <div class="bg-amber-50 dark:bg-amber-900/20 px-6 py-4 flex items-start gap-3">
              <ExclamationTriangleIcon class="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
              <div>
                <div class="font-semibold text-amber-800 dark:text-amber-200">{{ t('admin.upload.duplicate.heading') }}</div>
                <div class="font-mono text-xs text-amber-700 dark:text-amber-300 break-all mt-0.5">{{ sourceName(activeItem.source) }} → {{ activeItem.existingBook.id }}</div>
              </div>
            </div>
            <div class="p-6 flex gap-4">
              <CoverPreview :image-url="activeItem.existingBook.cover_url" size="small" :readonly="true" />
              <div class="flex-1 min-w-0 space-y-2">
                <div class="text-base font-semibold text-gray-900 dark:text-white">{{ activeItem.existingBook.title || '(no title)' }}</div>
                <div v-if="activeItem.existingBook.author" class="italic text-sm text-gray-600 dark:text-gray-400">{{ activeItem.existingBook.author }}</div>
                <div class="text-xs">
                  <span class="text-gray-500 dark:text-gray-400 mr-1">{{ t('admin.upload.duplicate.path_label') }}</span>
                  <code v-for="loc in activeItem.existingBook.locations" :key="loc" class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-[11px] mr-1 break-all">/{{ loc }}</code>
                </div>
                <div class="flex gap-2 mt-3">
                  <a
                    v-if="activeItem.existingBook.locations[0]"
                    :href="`/library/#/item/${activeItem.existingBook.locations[0]}`"
                    target="_blank" rel="noopener"
                    class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-blue-600 text-white text-sm hover:bg-blue-700"
                  >
                    <ArrowTopRightOnSquareIcon class="w-4 h-4" /> {{ t('admin.upload.duplicate.open') }}
                  </a>
                  <router-link
                    :to="`/admin/books?hash=${activeItem.existingBook.id}`"
                    class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
                  >{{ t('admin.upload.duplicate.edit') }}</router-link>
                  <button
                    @click="removeItem(activeItem)"
                    class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
                  >{{ t('admin.upload.batch.remove') }}</button>
                </div>
              </div>
            </div>
          </div>

          <!-- error (upload-level) -->
          <div
            v-else-if="activeItem.status === 'error'"
            class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-red-200 dark:border-red-900/60 overflow-hidden"
          >
            <div class="bg-red-50 dark:bg-red-900/20 px-6 py-4 flex items-start gap-3">
              <ExclamationTriangleIcon class="w-5 h-5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
              <div class="min-w-0">
                <div class="font-semibold text-red-800 dark:text-red-200">{{ sourceName(activeItem.source) }}</div>
                <div class="text-xs text-red-700 dark:text-red-300 mt-0.5 break-words">{{ activeItem.errorMsg }}</div>
              </div>
            </div>
            <div class="px-6 py-4 flex gap-2">
              <button
                @click="restageItem(activeItem)"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-blue-600 text-white text-sm hover:bg-blue-700"
              >
                <ArrowPathIcon class="w-4 h-4" /> {{ t('admin.upload.batch.retry') }}
              </button>
              <button
                @click="removeItem(activeItem)"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
              >
                <TrashIcon class="w-4 h-4" /> {{ t('admin.upload.batch.remove') }}
              </button>
            </div>
          </div>

          <!-- committed -->
          <div
            v-else-if="activeItem.status === 'committed' && activeItem.committedBook"
            class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-emerald-200 dark:border-emerald-900/60 overflow-hidden"
          >
            <div class="bg-gradient-to-b from-emerald-50 to-white dark:from-emerald-900/20 dark:to-gray-800 px-6 py-8 text-center">
              <div class="mx-auto w-14 h-14 rounded-full bg-emerald-500 text-white flex items-center justify-center">
                <CheckIcon class="w-8 h-8" />
              </div>
              <div class="mt-3 text-xl font-bold text-gray-900 dark:text-white">{{ t('admin.upload.success.heading') }}</div>
              <div class="text-sm text-gray-600 dark:text-gray-400 mt-1">{{ t('admin.upload.success.subtitle') }}</div>
            </div>
            <div class="p-6 flex gap-4">
              <CoverPreview :image-url="activeItem.committedBook.cover_url" size="small" :readonly="true" />
              <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs flex-1 min-w-0">
                <dt class="text-gray-500 dark:text-gray-400">{{ t('admin.upload.success.path') }}</dt>
                <dd class="font-mono text-gray-800 dark:text-gray-200 break-all">/{{ activeItem.committedBook.locations[0] }}</dd>
                <dt class="text-gray-500 dark:text-gray-400">{{ t('admin.upload.success.hash') }}</dt>
                <dd class="font-mono text-gray-800 dark:text-gray-200 break-all">{{ activeItem.committedBook.id }}</dd>
                <dt class="text-gray-500 dark:text-gray-400">{{ t('admin.upload.success.clearance') }}</dt>
                <dd><ClearancePill :value="activeItem.committedBook.clearance" /></dd>
              </dl>
            </div>
            <div class="px-6 py-4 bg-gray-50 dark:bg-gray-900/40 border-t border-gray-100 dark:border-gray-700 flex items-center justify-between">
              <a
                v-if="activeItem.committedBook.locations[0]"
                :href="`/library/#/item/${activeItem.committedBook.locations[0]}`"
                target="_blank" rel="noopener"
                class="inline-flex items-center gap-1.5 text-sm text-blue-600 dark:text-blue-400 hover:underline"
              >
                <ArrowTopRightOnSquareIcon class="w-4 h-4" /> {{ t('admin.upload.success.view') }}
              </a>
              <div class="flex gap-2">
                <router-link
                  :to="`/admin/books?hash=${activeItem.committedBook.id}`"
                  class="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
                >{{ t('admin.upload.success.edit') }}</router-link>
                <button
                  @click="openPicker"
                  class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-blue-600 text-white text-sm hover:bg-blue-700"
                >
                  <ArrowUpTrayIcon class="w-4 h-4" /> {{ t('admin.upload.success.upload_another') }}
                </button>
              </div>
            </div>
          </div>

          <!-- staged / committing → editor. No :key — reusing the instance across
               same-format tab switches keeps the user's resized preview height
               (a fresh mount would reset it) and avoids needless remounts. -->
          <UploadItemEditor
            v-else
            :item="activeItem"
            :single="items.length === 1"
            @commit="onItemCommit(activeItem)"
            @remove="removeItem(activeItem)"
            @view-log="viewLog(activeItem)"
          />
        </template>
      </main>
    </div>

    <!-- hidden file input (multiple) -->
    <input ref="fileInputEl" type="file" multiple :accept="ACCEPT_ATTR" class="hidden" @change="onFileChosen" />

    <!-- ===== LOG MODAL ===== -->
    <div
      v-if="showLogModal && logModalItem"
      class="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6"
      @click.self="showLogModal = false"
    >
      <div class="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col">
        <div class="flex items-center justify-between p-4 border-b border-gray-100 dark:border-gray-700">
          <h3 class="text-sm font-semibold text-gray-900 dark:text-white truncate">
            {{ t('admin.upload.log.heading') }} — {{ sourceName(logModalItem.source) }}
          </h3>
          <button @click="showLogModal = false" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
            <XMarkIcon class="w-5 h-5" />
          </button>
        </div>
        <div class="flex-1 overflow-y-auto bg-gray-900 dark:bg-black text-gray-200 font-mono text-xs p-3">
          <div v-if="!logModalItem.log.length" class="text-gray-500">—</div>
          <div v-for="(entry, i) in logModalItem.log" :key="i" class="whitespace-pre-wrap break-all">
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
</style>
