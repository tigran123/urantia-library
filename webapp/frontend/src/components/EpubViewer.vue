<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useImmersive } from '../composables/useImmersive'
import { useLineHeight, LINE_HEIGHT_OPTIONS } from '../composables/useLineHeight'
import ePub, { Book, Rendition, type Location } from 'epubjs'
import type { NavItem } from 'epubjs/types/navigation'
import api from '../api'
import { useI18n } from 'vue-i18n'
import {
  Bars3Icon,
  ChevronDoubleLeftIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
} from '@heroicons/vue/24/outline'
import EpubTocNode from './EpubTocNode.vue'
import AnnotationPopover from './AnnotationPopover.vue'
import AnnotationsSidebar from './AnnotationsSidebar.vue'
import AnnotationVisibilityToggle from './AnnotationVisibilityToggle.vue'
import { useAnnotations } from '../composables/useAnnotations'
import { anchorFromSelection as epubAnchorFromSelection, paintAnnotations as epubPaintAnnotations } from '../lib/anchors/epub'
import { popoverPositionFromViewport, type PopoverPosition } from '../lib/anchors/popoverPosition'
import type { Annotation } from '../api'
import { viewerUrls, sourceHashId, sourceLoadKey, type ViewerSource } from './viewerSource'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{ source: ViewerSource; embedded?: boolean }>()
const hashId = computed(() => sourceHashId(props.source))

const viewer = ref<HTMLElement | null>(null)
let book: Book | null = null
let rendition: Rendition | null = null
let resizeObs: ResizeObserver | null = null
let themeObs: MutationObserver | null = null
let resizeTimer: ReturnType<typeof setTimeout> | null = null

const loading = ref(true)
const error = ref('')
const toc = ref<NavItem[]>([])
const tocOpen = ref(false)
const bookTitle = ref('')
const bookAuthors = ref<string[]>([])
const { immersive, toggleImmersive } = useImmersive()

const annotationsApi = useAnnotations(hashId)
const annoSidebarOpen = ref(false)

interface EpubPending {
  selectedText: string
  prefix: string
  suffix: string
  anchor: { type: 'epub'; cfiRange: string }
}
const annoPending = ref<EpubPending | null>(null)
const annoExisting = ref<Annotation | null>(null)
const annoPosition = ref<PopoverPosition | null>(null)

const closeAnnoPopover = () => {
  annoPending.value = null
  annoExisting.value = null
  annoPosition.value = null
}

// Compute the popover's anchor rect (in viewer-pane viewport coords) from a
// Range that lives inside the EPUB iframe. The iframe's bounding rect gives
// us the offset back into the parent doc.
const positionFromIframeRange = (range: Range): PopoverPosition | null => {
  if (!viewer.value) return null
  const iframe = viewer.value.querySelector('iframe')
  if (!iframe) return null
  const ir = iframe.getBoundingClientRect()
  const rr = range.getBoundingClientRect()
  const rect = {
    left: ir.left + rr.left,
    right: ir.left + rr.right,
    top: ir.top + rr.top,
    bottom: ir.top + rr.bottom,
    width: rr.width,
    height: rr.height,
  }
  return popoverPositionFromViewport(rect, viewer.value)
}

const onAnnoClick = (id: number) => {
  const found = annotationsApi.annotations.value.find(a => a.id === id) || null
  annoExisting.value = found
  annoPending.value = null
  // Ask ePub.js for the CFI's range so we can anchor beside it. The range
  // lives inside the current section iframe.
  if (found && rendition) {
    try {
      const cfi = (found.anchor as { cfiRange?: string }).cfiRange
      if (cfi) {
        const range = (rendition as any).getRange?.(cfi)
        if (range) annoPosition.value = positionFromIframeRange(range)
      }
    } catch { /* stale CFI */ }
  }
}

const repaintEpubAnnotations = () => {
  if (!rendition) return
  epubPaintAnnotations(annotationsApi.visible.value, {
    rendition,
    onClick: onAnnoClick,
  })
}

const onEpubSelected = (cfiRange: string, contents: any) => {
  if (!rendition || !contents) return
  // Foreign files (not in the library DB) can't store annotations — leave the
  // native selection alone, don't pop our UI.
  if (!annotationsApi.enabled.value) return
  // The iframe's selection still owns the Range we need for prefix/suffix.
  const win = contents.window as Window | undefined
  const sel = win?.getSelection?.()
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return
  const range = sel.getRangeAt(0)
  const anchored = epubAnchorFromSelection(cfiRange, range)
  if (!anchored) return

  annoPosition.value = positionFromIframeRange(range)

  // Dedup: same CFI range from the same user → open existing for editing.
  const dup = annotationsApi.annotations.value.find(a => {
    const aa = a.anchor as { type?: string; cfiRange?: string }
    return a.is_own && aa.type === 'epub' && aa.cfiRange === anchored.anchor.cfiRange
  })
  if (dup) {
    annoExisting.value = dup
    annoPending.value = null
    return
  }

  annoPending.value = {
    selectedText: anchored.selectedText,
    prefix: anchored.prefix,
    suffix: anchored.suffix,
    anchor: anchored.anchor,
  }
  annoExisting.value = null
}

const onAnnoSaveCreate = async (payload: { body: string | null; isPublic: boolean }) => {
  if (!annoPending.value) return
  try {
    await annotationsApi.create(annoPending.value.anchor, annoPending.value.selectedText, {
      body: payload.body,
      isPublic: payload.isPublic,
      prefix: annoPending.value.prefix,
      suffix: annoPending.value.suffix,
    })
    closeAnnoPopover()
    repaintEpubAnnotations()
  } catch (e: any) {
    console.error('Failed to save annotation', e)
    alert(e.response?.data?.detail || e.message || 'Failed to save annotation')
  }
}

const onAnnoUpdate = async (payload: { id: number; body: string | null; isPublic: boolean }) => {
  try {
    await annotationsApi.update(payload.id, { body: payload.body, is_public: payload.isPublic })
    closeAnnoPopover()
    repaintEpubAnnotations()
  } catch (e: any) {
    console.error('Failed to update annotation', e)
    alert(e.response?.data?.detail || e.message || 'Failed to update annotation')
  }
}

const onAnnoDelete = async (id: number) => {
  try {
    await annotationsApi.remove(id)
    closeAnnoPopover()
    repaintEpubAnnotations()
  } catch (e: any) {
    console.error('Failed to delete annotation', e)
    alert(e.response?.data?.detail || e.message || 'Failed to delete annotation')
  }
}

const onAnnoJump = (a: Annotation) => {
  const anchor = a.anchor as { type: string; cfiRange: string }
  if (anchor.type !== 'epub' || !anchor.cfiRange || !rendition) return
  rendition.display(anchor.cfiRange).then(() => {
    annoExisting.value = a
    annoPending.value = null
  }).catch(() => { /* stale CFI */ })
}

watch(() => annotationsApi.visible.value, () => {
  repaintEpubAnnotations()
})

const FONT_SCALE_KEY = 'reader-font-scale'
const FONT_FAMILY_KEY = 'reader-font-family'
const FONT_OPTIONS = [
  { id: 'sans',  label: 'Sans',  stack: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' },
  { id: 'serif', label: 'Serif', stack: 'Georgia, "Times New Roman", "Liberation Serif", serif' },
  { id: 'mono',  label: 'Mono',  stack: '"SF Mono", Menlo, Consolas, "Liberation Mono", monospace' },
]
const loadFontScale = (): number => {
  const raw = localStorage.getItem(FONT_SCALE_KEY)
  const n = raw ? parseFloat(raw) : NaN
  return Number.isFinite(n) && n >= 0.6 && n <= 2 ? n : 1
}
const loadFontFamilyId = (): string => {
  const raw = localStorage.getItem(FONT_FAMILY_KEY) || ''
  return FONT_OPTIONS.find(o => o.id === raw)?.id || 'sans'
}
const fontScale = ref(loadFontScale())
const fontFamilyId = ref(loadFontFamilyId())
const fontFamily = computed(
  () => FONT_OPTIONS.find(o => o.id === fontFamilyId.value)?.stack || FONT_OPTIONS[0].stack
)
watch(fontScale, (v) => { try { localStorage.setItem(FONT_SCALE_KEY, String(v)) } catch {} })
watch(fontFamilyId, (v) => { try { localStorage.setItem(FONT_FAMILY_KEY, v) } catch {} })

const { lineHeight, onLineHeightChange } = useLineHeight(
  (mutate) => reflowAtCurrentLocation(mutate)
)

const applyTypography = () => {
  if (!rendition) return
  rendition.themes.fontSize(`${Math.round(fontScale.value * 100)}%`)
  rendition.themes.font(fontFamily.value)
  rendition.themes.override('line-height', lineHeight.value)
}

// epub.js's `currentLocation()` actually returns the same `Location` shape
// emitted by `relocated`, but its TS types say `DisplayedLocation`. Cast
// through `unknown` to bypass the (incorrect) overlap check.
const currentCfi = (): string | undefined => {
  if (!rendition) return undefined
  const loc = rendition.currentLocation() as unknown as Location | undefined
  return loc?.start?.cfi
}

// Wrap mutations so the reader stays on the same content position after
// epub.js re-paginates. Capture CFI before the mutation, then redisplay
// at that CFI once the new layout settles.
const reflowAtCurrentLocation = (mutate: () => void) => {
  if (!rendition) { mutate(); return }
  const cfi = currentCfi()
  mutate()
  applyTypography()
  if (!cfi) return
  let done = false
  const restore = () => {
    if (done) return
    done = true
    rendition!.off('relocated', restore)
    rendition!.display(cfi).catch(() => { /* CFI may be stale */ })
  }
  rendition.on('relocated', restore)
  setTimeout(restore, 200)
}

const incFont = () => reflowAtCurrentLocation(() => { fontScale.value = Math.min(2, fontScale.value + 0.1) })
const decFont = () => reflowAtCurrentLocation(() => { fontScale.value = Math.max(0.6, fontScale.value - 0.1) })
const resetFont = () => reflowAtCurrentLocation(() => { fontScale.value = 1 })
const onFontFamilyChange = (e: Event) => {
  const v = (e.target as HTMLSelectElement).value
  reflowAtCurrentLocation(() => { fontFamilyId.value = v })
}

// True once `book.locations.generate()` has finished — only then does
// `percentageFromCfi` return a meaningful book-wide position. Generation runs
// in the background after the EPUB is displayed so it doesn't block the first
// paint.
let locationsReady = false

const saveProgress = async (cfi: string) => {
  if (!cfi || !hashId.value) return
  let percent: number | null = null
  if (locationsReady && book) {
    try {
      const p = book.locations.percentageFromCfi(cfi)
      if (Number.isFinite(p)) percent = Math.max(0, Math.min(1, p))
    } catch { /* stale CFI / not in locations index */ }
  }
  try {
    await api.post('/progress', { hash_id: hashId.value, location: cfi, percent })
  } catch (e) {
    console.error('Failed to save progress', e)
  }
}

const loadProgress = async () => {
  if (!hashId.value) return null
  try {
    const res = await api.get(`/progress/${encodeURIComponent(hashId.value)}`)
    return res.data.location
  } catch (e: any) {
    if (e.response?.status !== 404) console.error('Failed to load progress', e)
    return null
  }
}

let saveTimeout: any
let annoReloadTimeout: any

// !important on width/height/object-fit because many epubs bake
// `width:100%;height:100%` onto the cover <img>, stretching it on landscape.
// Zero margin/padding on html/body so epub.js's column width matches what
// the user sees — previously a few characters were getting clipped at the
// right edge on narrow viewports.
const themeRules = (isDark: boolean) => ({
  'html, body': {
    margin: '0 !important',
    padding: '0 !important',
    'box-sizing': 'border-box !important',
  },
  body: {
    background: isDark ? '#111827' : '#ffffff',
    color: isDark ? '#f3f4f6' : '#111827',
    '-webkit-hyphens': 'auto !important',
    hyphens: 'auto !important',
    'overflow-wrap': 'break-word',
    'text-rendering': 'optimizeLegibility',
    'font-kerning': 'normal',
    widows: '2',
    orphans: '2',
  },
  'p, li, dd, dt, blockquote': {
    '-webkit-hyphens': 'auto !important',
    hyphens: 'auto !important',
    'text-align': 'justify !important',
    'overflow-wrap': 'break-word !important',
  },
  a: { color: isDark ? '#60a5fa' : '#2563eb' },
  img: {
    'max-width': '100% !important',
    'max-height': '100% !important',
    'object-fit': 'contain !important',
    display: 'block',
    margin: '0 auto',
  },
  svg: {
    'max-width': '100% !important',
    'max-height': '100% !important',
    display: 'block',
    margin: '0 auto',
  },
})

const applyTheme = () => {
  if (!rendition) return
  const isDark = document.documentElement.classList.contains('dark')
  const name = isDark ? 'dark' : 'light'
  rendition.themes.register(name, themeRules(isDark))
  rendition.themes.select(name)
}

const onTocNavigate = (href: string) => {
  if (rendition && href) rendition.display(href).catch(() => {})
  if (immersive.value) tocOpen.value = false
}

const prevPage = () => { if (rendition) rendition.prev() }
const nextPage = () => { if (rendition) rendition.next() }

const onKeyDown = (e: KeyboardEvent) => {
  if (!immersive.value) return
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
  if (e.key === 'Escape') { e.preventDefault(); immersive.value = false }
  else if (e.key === 'PageDown') { e.preventDefault(); nextPage() }
  else if (e.key === 'PageUp') { e.preventDefault(); prevPage() }
}

// The ResizeObserver picks up the dimensional change and handles
// repagination, so no explicit resize+redisplay is needed here — calling
// resize() ourselves caused the rendition to drop the current CFI.
// immediate so a refresh into immersive (immersive=1 in the URL) starts with
// the TOC closed, matching a live toggle.
watch(immersive, (v) => {
  if (v) tocOpen.value = false
}, { immediate: true })

const initEpub = async () => {
  if (!viewer.value) return

  loading.value = true
  error.value = ''
  toc.value = []
  bookTitle.value = ''
  bookAuthors.value = []

  try {
    const res = await api.get(viewerUrls(props.source).file, {
      responseType: 'arraybuffer'
    })

    book = ePub(res.data as ArrayBuffer)

    // Pass explicit pixel dimensions so epub.js's first pagination uses the
    // real container size. Avoids the right-edge text clipping seen on
    // narrow mobile viewports when relying on width:'100%' alone.
    const initW = viewer.value.clientWidth
    const initH = viewer.value.clientHeight
    rendition = book.renderTo(viewer.value, {
      width: initW,
      height: initH,
      spread: 'none',
      manager: 'continuous',
      flow: 'paginated'
    })

    applyTheme()
    applyTypography()

    await book.ready
    toc.value = book.navigation?.toc || []
    bookTitle.value = book.packaging?.metadata?.title || ''
    const creator = book.packaging?.metadata?.creator
    bookAuthors.value = creator ? [creator] : []

    const savedLocation = await loadProgress()
    if (savedLocation) {
      await rendition.display(savedLocation)
    } else {
      await rendition.display()
    }

    rendition.on('relocated', (location: Location) => {
      clearTimeout(saveTimeout)
      saveTimeout = setTimeout(() => {
        saveProgress(location.start.cfi)
      }, 1000)
      // ePub.js drops its highlight overlays whenever a section reloads, so
      // re-paint after each navigation event.
      repaintEpubAnnotations()
      // Another user may have created or deleted annotations since we opened
      // the book; refresh from the server and repaint. Debounced so rapid
      // page-flipping doesn't hammer the backend.
      clearTimeout(annoReloadTimeout)
      annoReloadTimeout = setTimeout(() => {
        annotationsApi.load().then(repaintEpubAnnotations)
      }, 1000)
    })

    rendition.on('selected', onEpubSelected)

    await annotationsApi.load()
    repaintEpubAnnotations()

    // Build the locations index in the background so percentageFromCfi works.
    // 1024 chars per location is epub.js's recommended granularity (~one page
    // of text). Can take a few seconds on large books — runs detached so the
    // first paint isn't blocked. Once ready, re-save with the now-known
    // percent so the bookshelf bar updates without waiting for the user to
    // turn a page.
    book.locations.generate(1024).then(() => {
      locationsReady = true
      const cfi = currentCfi()
      if (cfi) saveProgress(cfi)
    }).catch(() => { /* malformed epub — leave percent as null */ })

    // After a window resize, epub.js leaves its paginator in an inconsistent
    // state — Prev/Next stop working until reload. Forward the new size and
    // redisplay at the current CFI so pagination is rebuilt around the same
    // content. Debounced so it only fires once the user stops dragging.
    // Seed lastW/lastH with the current dimensions so the initial spurious
    // observe() callback (which fires with no actual size change) is
    // ignored — it would otherwise clobber the just-restored saved CFI.
    let lastW = viewer.value.clientWidth
    let lastH = viewer.value.clientHeight
    resizeObs?.disconnect()
    resizeObs = new ResizeObserver(() => {
      if (!viewer.value) return
      const w = viewer.value.clientWidth
      const h = viewer.value.clientHeight
      if (w === lastW && h === lastH) return
      lastW = w
      lastH = h
      if (resizeTimer) clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => {
        if (!rendition || !viewer.value) return
        const cfi = currentCfi()
        rendition.resize(viewer.value.clientWidth, viewer.value.clientHeight)
        if (cfi) rendition.display(cfi).catch(() => {})
      }, 150)
    })
    resizeObs.observe(viewer.value)

  } catch (e: any) {
    error.value = e.message || 'Failed to load EPUB'
  } finally {
    loading.value = false
  }
}

const destroyBook = () => {
  resizeObs?.disconnect()
  resizeObs = null
  if (resizeTimer) { clearTimeout(resizeTimer); resizeTimer = null }
  if (book) {
    book.destroy()
    book = null
    rendition = null
  }
}

onMounted(() => {
  initEpub()
  themeObs = new MutationObserver(() => applyTheme())
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  window.addEventListener('keydown', onKeyDown)
})

watch(() => sourceLoadKey(props.source), () => {
  destroyBook()
  initEpub()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  clearTimeout(saveTimeout)
  clearTimeout(annoReloadTimeout)
  themeObs?.disconnect()
  themeObs = null
  destroyBook()
})
</script>

<template>
  <div
    :class="[
      'epub-viewer flex flex-col items-stretch bg-gray-100 dark:bg-gray-800 w-full overscroll-contain',
      immersive
        ? 'fixed inset-0 z-50 h-dvh rounded-none'
        : (embedded ? 'relative h-[60vh] max-h-[700px] rounded-lg shadow' : 'relative h-[80vh] rounded-lg shadow')
    ]"
  >
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-black/50 z-30 rounded-lg">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
    </div>
    <div v-else-if="error" class="absolute inset-0 flex items-center justify-center bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded-lg z-30 p-4 text-center">
      {{ error }}
    </div>

    <!-- Normal toolbar (hidden in immersive) -->
    <div v-if="!immersive" class="w-full flex flex-wrap items-center justify-between p-2 sm:p-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg shadow-sm gap-2 z-20">
      <div class="flex items-center gap-1 sm:gap-2">
        <button @click="prevPage" :title="t('search.previous')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors">
          <ChevronLeftIcon class="h-5 w-5" />
        </button>
        <button @click="nextPage" :title="t('search.next')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors">
          <ChevronRightIcon class="h-5 w-5" />
        </button>
      </div>
      <div class="text-sm text-gray-700 dark:text-gray-300 truncate min-w-0 hidden md:block">
        <span v-if="bookTitle" class="font-semibold">{{ bookTitle }}</span>
        <span v-if="bookAuthors.length" class="ml-2 text-gray-500 dark:text-gray-400">— {{ bookAuthors.join(', ') }}</span>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-1 sm:gap-2">
        <select
          :value="fontFamilyId"
          @change="onFontFamilyChange"
          :title="t('app.font_family')"
          class="px-1 sm:px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm cursor-pointer border-0 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option v-for="o in FONT_OPTIONS" :key="o.id" :value="o.id" :style="{ fontFamily: o.stack }">{{ o.label }}</option>
        </select>
        <select
          :value="lineHeight"
          @change="onLineHeightChange"
          :title="t('app.line_spacing')"
          class="px-1 sm:px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm cursor-pointer border-0 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option v-for="o in LINE_HEIGHT_OPTIONS" :key="o.id" :value="o.id">{{ t(o.labelKey) }}</option>
        </select>
        <button @click="decFont" :title="t('app.font_smaller')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm">A−</button>
        <button @click="resetFont" :title="t('app.font_reset')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm">A</button>
        <button @click="incFont" :title="t('app.font_larger')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm">A+</button>
        <AnnotationVisibilityToggle v-model="annotationsApi.visibility.value" />
        <button @click="toggleImmersive" :title="t('app.immersive_enter')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded">
          <ArrowsPointingOutIcon class="h-5 w-5" />
        </button>
      </div>
    </div>

    <!-- TOC sidebar + Rendition -->
    <div class="flex flex-row flex-grow min-h-0 overflow-hidden" :class="immersive ? '' : 'rounded-b-lg'">
      <!-- Non-immersive sidebar -->
      <aside
        v-if="!immersive"
        class="shrink-0 flex flex-col border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 transition-[width] duration-200"
        :class="tocOpen ? 'w-64' : 'w-9'"
      >
        <div class="flex items-center p-1.5 border-b border-gray-200 dark:border-gray-700" :class="tocOpen ? 'justify-between' : 'justify-center'">
          <span v-if="tocOpen" class="px-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">{{ t('app.toc') }}</span>
          <button
            @click="tocOpen = !tocOpen"
            class="p-1 rounded text-gray-600 hover:bg-gray-200 dark:text-gray-300 dark:hover:bg-gray-700"
            :title="tocOpen ? t('app.toc_collapse') : t('app.toc_expand')"
          >
            <ChevronDoubleLeftIcon v-if="tocOpen" class="w-4 h-4" />
            <Bars3Icon v-else class="w-4 h-4" />
          </button>
        </div>
        <nav v-if="tocOpen" class="flex-grow overflow-auto p-1 text-gray-800 dark:text-gray-200">
          <p v-if="!toc.length" class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">{{ t('app.toc_empty') }}</p>
          <EpubTocNode
            v-for="(entry, i) in toc"
            :key="i"
            :entry="entry"
            :level="0"
            @navigate="onTocNavigate"
          />
        </nav>
        <div v-if="tocOpen" class="shrink-0 border-t border-gray-200 dark:border-gray-700">
          <button
            @click="annoSidebarOpen = !annoSidebarOpen"
            class="w-full flex items-center justify-between px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
          >
            <span>{{ t('app.annotations') }} <span class="text-gray-400">({{ annotationsApi.visible.value.length }})</span></span>
            <span>{{ annoSidebarOpen ? '▾' : '▸' }}</span>
          </button>
          <div v-if="annoSidebarOpen" class="max-h-64 overflow-auto p-1">
            <AnnotationsSidebar
              :annotations="annotationsApi.visible.value"
              @jump="onAnnoJump"
            />
          </div>
        </div>
      </aside>

      <div class="relative flex-grow min-w-0 bg-white dark:bg-gray-900 overflow-hidden">
        <div ref="viewer" class="absolute inset-0" :class="immersive ? 'p-0' : 'p-1 sm:p-2 lg:p-3'"></div>
        <AnnotationPopover
          :pending="annoPending"
          :existing="annoExisting"
          :position="annoPosition"
          :can-edit="annoExisting?.is_own ?? false"
          @save="onAnnoSaveCreate"
          @update="onAnnoUpdate"
          @delete="onAnnoDelete"
          @close="closeAnnoPopover"
        />

        <!-- Immersive floating controls -->
        <template v-if="immersive">
          <!-- TOC overlay drawer -->
          <aside
            v-if="tocOpen"
            class="absolute inset-y-0 left-0 z-40 w-64 max-w-[80%] bg-gray-50 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 shadow-xl flex flex-col"
          >
            <div class="flex items-center justify-between p-1.5 border-b border-gray-200 dark:border-gray-700">
              <span class="px-2 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">{{ t('app.toc') }}</span>
              <button
                @click="tocOpen = false"
                class="p-1 rounded text-gray-600 hover:bg-gray-200 dark:text-gray-300 dark:hover:bg-gray-700"
                :title="t('app.toc_collapse')"
              >
                <ChevronDoubleLeftIcon class="w-4 h-4" />
              </button>
            </div>
            <!-- pb-16: clear the floating prev-page button so the last TOC
                 entries (and their expand arrows) stay tappable. -->
            <nav class="flex-grow overflow-auto p-1 pb-16 text-gray-800 dark:text-gray-200">
              <p v-if="!toc.length" class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">{{ t('app.toc_empty') }}</p>
              <EpubTocNode
                v-for="(entry, i) in toc"
                :key="i"
                :entry="entry"
                :level="0"
                @navigate="onTocNavigate"
              />
            </nav>
          </aside>

          <!-- Top-left: TOC toggle -->
          <button
            v-if="!tocOpen"
            @click="tocOpen = true"
            :title="t('app.toc_expand')"
            class="absolute top-2 left-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white"
          >
            <Bars3Icon class="h-5 w-5" />
          </button>

          <!-- Top-right: exit immersive -->
          <button
            @click="toggleImmersive"
            :title="t('app.immersive_exit')"
            class="absolute top-2 right-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white"
          >
            <ArrowsPointingInIcon class="h-5 w-5" />
          </button>

          <!-- Bottom-left: previous page -->
          <button
            @click="prevPage"
            :title="t('search.previous')"
            class="absolute bottom-3 left-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white"
          >
            <ChevronLeftIcon class="h-6 w-6" />
          </button>

          <!-- Bottom-right: next page -->
          <button
            @click="nextPage"
            :title="t('search.next')"
            class="absolute bottom-3 right-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white"
          >
            <ChevronRightIcon class="h-6 w-6" />
          </button>
        </template>
      </div>
    </div>
  </div>
</template>
