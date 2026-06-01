<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import type { PDFDocumentProxy, PDFPageProxy, RenderTask, PageViewport } from 'pdfjs-dist'
import api from '../api'
import { useI18n } from 'vue-i18n'
import {
  Bars3Icon,
  BookOpenIcon,
  DocumentIcon,
  ChevronDoubleLeftIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  ArrowLongLeftIcon,
  ArrowLongRightIcon,
  ArrowsRightLeftIcon,
  ArrowsUpDownIcon,
  MagnifyingGlassPlusIcon,
  MagnifyingGlassMinusIcon,
  Bars3BottomLeftIcon,
} from '@heroicons/vue/24/outline'
import PdfTocNode, { type PdfOutlineNode } from './PdfTocNode.vue'
import AnnotationPopover from './AnnotationPopover.vue'
import AnnotationsSidebar from './AnnotationsSidebar.vue'
import AnnotationVisibilityToggle from './AnnotationVisibilityToggle.vue'
import { useAnnotations } from '../composables/useAnnotations'
import { useScrollPan } from '../composables/useScrollPan'
import { anchorFromSelection as pdfAnchorFromSelection, paintAnnotations as pdfPaintAnnotations } from '../lib/anchors/pdf'
import { popoverPosition, type PopoverPosition } from '../lib/anchors/popoverPosition'
import type { Annotation } from '../api'
import { viewerUrls, sourceHashId, sourceLoadKey, type ViewerSource } from './viewerSource'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{ source: ViewerSource }>()
const hashId = computed(() => sourceHashId(props.source))

const canvas = ref<HTMLCanvasElement | null>(null)
const canvas2 = ref<HTMLCanvasElement | null>(null)
const textLayer = ref<HTMLElement | null>(null)
const textLayer2 = ref<HTMLElement | null>(null)
const container = ref<HTMLElement | null>(null)

let pdfDoc: PDFDocumentProxy | null = null
let activeTasks: RenderTask[] = []
let activeTextLayers: pdfjsLib.TextLayer[] = []
let resizeObs: ResizeObserver | null = null
let resizeTimer: ReturnType<typeof setTimeout> | null = null
let saveTimer: ReturnType<typeof setTimeout> | null = null

const loading = ref(true)
const loadingPage = ref(false)
const error = ref('')
const totalPages = ref(0)
const currentPage = ref(1)
const renderedScale = ref(1)
// Editable zoom readout. Width is fixed via `w-14 tabular-nums` — a
// variable-width label reflows the toolbar, whose height feeds back into the
// fit scale and can oscillate forever.
const zoomInput = ref('100')
const formatScale = (s: number) =>
  s >= 1 ? Math.round(s * 100).toString() : (s * 100).toFixed(1)
watch(renderedScale, (s) => { zoomInput.value = formatScale(s) }, { immediate: true })
const commitZoomInput = () => {
  // Guard: when commit triggers a render, loadingPage flips to true → the
  // :disabled binding force-blurs the focused input → @blur fires another
  // commit. Skip the second one (pdf.js errors on concurrent renders to the
  // same canvas).
  if (loadingPage.value) return
  const raw = zoomInput.value.replace(',', '.').replace('%', '').trim()
  const pct = parseFloat(raw)
  if (!Number.isFinite(pct) || pct <= 0) {
    zoomInput.value = formatScale(renderedScale.value)
    return
  }
  // Guard only against absurd values; users can freely enter anything sane.
  const scale = Math.min(100, Math.max(0.001, pct / 100))
  // No-op when the typed value already matches the current scale. Without the
  // fitMode check, this also makes Escape→restore-on-blur a no-op when the
  // user was in fit-width/-height mode.
  if (Math.abs(scale - renderedScale.value) < 1e-6) {
    zoomInput.value = formatScale(renderedScale.value)
    return
  }
  customScale.value = scale
  fitMode.value = 'custom'
  renderPage(currentPage.value)
}
const onZoomEnter = (e: Event) => {
  // Route Enter through blur so commit runs through a single path; otherwise
  // the synthetic blur from :disabled flipping mid-render would commit twice.
  ;(e.currentTarget as HTMLInputElement).blur()
}
const cancelZoomInput = (e: Event) => {
  zoomInput.value = formatScale(renderedScale.value)
  ;(e.currentTarget as HTMLInputElement).blur()
}
const isDoublePage = ref(false)
// Whether odd page numbers fall on the right (evince default): spreads run
// [1] [2,3] [4,5]…. When false, odd pages fall on the left: [1,2] [3,4]….
const oddOnRight = ref(true)
// The two pages of the spread currently on screen; either may be null (the
// lone cover in odd-right mode, or a lone last page).
const leftPage = ref<number | null>(null)
const rightPage = ref<number | null>(null)

// 'width' / 'height': re-fit to container on every render (and on resize).
// 'custom': user picked a zoom level; persist verbatim.
const fitMode = ref<'width' | 'height' | 'custom'>('width')
const customScale = ref(1)

const toc = ref<PdfOutlineNode[]>([])
const tocOpen = ref(false)
const immersive = ref(false)
const textSelectEnabled = ref(false)

const annotationsApi = useAnnotations(hashId)
const annoSidebarOpen = ref(false)

// Drag-to-pan when the page overflows the scroll container. Veto when the
// mousedown lands in the text-selection layer (so highlighting still works)
// or in the annotation popover.
const { isDragging: isPanning, canPan, onMouseDown: onPanMouseDown, updateCanPan } = useScrollPan(
  container,
  (e) => {
    const t = e.target as HTMLElement | null
    if (!t) return true
    if (t.closest('.textLayer')) return false
    if (t.closest('.anno-popover')) return false
    return true
  },
)

interface PdfPending {
  selectedText: string
  prefix: string
  suffix: string
  anchor: { type: 'pdf'; page: number; start: { spanIndex: number; offset: number }; end: { spanIndex: number; offset: number } }
}
const annoPending = ref<PdfPending | null>(null)
const annoExisting = ref<Annotation | null>(null)
const annoPosition = ref<PopoverPosition | null>(null)

const closeAnnoPopover = () => {
  annoPending.value = null
  annoExisting.value = null
  annoPosition.value = null
}

const onAnnoClick = (id: number, ev: MouseEvent) => {
  const found = annotationsApi.annotations.value.find(a => a.id === id) || null
  annoExisting.value = found
  annoPending.value = null
  // Anchor popover beside the clicked mark using the page container as the
  // reference frame.
  const mark = (ev.target as HTMLElement | null)?.closest?.('mark.anno') as HTMLElement | null
  if (mark && container.value) {
    annoPosition.value = popoverPosition(mark.getBoundingClientRect(), container.value)
  }
}

const paintTextLayerAnnotations = () => {
  const list = annotationsApi.visible.value
  if (textLayer.value && leftPage.value !== null) {
    pdfPaintAnnotations(textLayer.value, list, { page: leftPage.value, onClick: onAnnoClick })
  }
  if (textLayer2.value && rightPage.value !== null) {
    pdfPaintAnnotations(textLayer2.value, list, { page: rightPage.value, onClick: onAnnoClick })
  }
}

const samePdfAnchor = (a: any, b: any): boolean =>
  a?.type === 'pdf' && b?.type === 'pdf'
  && a.page === b.page
  && a.start?.spanIndex === b.start?.spanIndex && a.start?.offset === b.start?.offset
  && a.end?.spanIndex === b.end?.spanIndex && a.end?.offset === b.end?.offset

// Examine the current selection (if any) and open the popover. Either we
// have an existing own annotation with this exact anchor (open in edit mode),
// or we don't (open in create mode). Shared between the per-text-layer
// mouseup hook and the document-wide selectionchange hook used for touch.
const tryShowAnnotationPopoverFromSelection = () => {
  if (!textSelectEnabled.value) return
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return
  const range = sel.getRangeAt(0)
  let layer: HTMLElement | null = null
  let page: number | null = null
  if (textLayer.value && textLayer.value.contains(range.startContainer) && textLayer.value.contains(range.endContainer)) {
    layer = textLayer.value
    page = leftPage.value
  } else if (textLayer2.value && textLayer2.value.contains(range.startContainer) && textLayer2.value.contains(range.endContainer)) {
    layer = textLayer2.value
    page = rightPage.value
  }
  if (!layer || page === null) return
  if ((range.startContainer.parentElement || range.startContainer as HTMLElement | null)?.closest?.('.anno-popover')) return

  const anchored = pdfAnchorFromSelection(layer, page, range)
  if (!anchored) return

  const scroll = container.value
  if (!scroll) return
  annoPosition.value = popoverPosition(range.getBoundingClientRect(), scroll)

  // Dedup: if we already own an annotation with the same anchor, open it for
  // editing rather than creating an unreachable duplicate.
  const dup = annotationsApi.annotations.value.find(a =>
    a.is_own && a.selected_text === anchored.selectedText && samePdfAnchor(a.anchor, anchored.anchor),
  )
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

const onTextLayerMouseUp = (e: MouseEvent, _slot: 'left' | 'right') => {
  if (!textSelectEnabled.value) return
  const target = e.target as HTMLElement | null
  if (target?.closest('.anno-popover')) return
  if (target?.closest('mark.anno')) return
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
    if (!annoExisting.value) closeAnnoPopover()
    return
  }
  tryShowAnnotationPopoverFromSelection()
}

// Catches touch-driven selection on Android (where mouseup doesn't fire) and
// selection-handle adjustments after a desktop drag.
let pdfSelDebounce: number | null = null
const onPdfSelectionChange = () => {
  if (pdfSelDebounce !== null) window.clearTimeout(pdfSelDebounce)
  pdfSelDebounce = window.setTimeout(() => {
    pdfSelDebounce = null
    tryShowAnnotationPopoverFromSelection()
  }, 350)
}
onMounted(() => document.addEventListener('selectionchange', onPdfSelectionChange))
onBeforeUnmount(() => {
  if (pdfSelDebounce !== null) window.clearTimeout(pdfSelDebounce)
  document.removeEventListener('selectionchange', onPdfSelectionChange)
})

const onAnnoSaveCreate = async (payload: { body: string | null; isPublic: boolean }) => {
  if (!annoPending.value) return
  try {
    await annotationsApi.create(annoPending.value.anchor, annoPending.value.selectedText, {
      body: payload.body,
      isPublic: payload.isPublic,
      prefix: annoPending.value.prefix,
      suffix: annoPending.value.suffix,
    })
    window.getSelection()?.removeAllRanges()
    closeAnnoPopover()
    paintTextLayerAnnotations()
  } catch (e: any) {
    console.error('Failed to save annotation', e)
    alert(e.response?.data?.detail || e.message || 'Failed to save annotation')
  }
}

const onAnnoUpdate = async (payload: { id: number; body: string | null; isPublic: boolean }) => {
  try {
    await annotationsApi.update(payload.id, { body: payload.body, is_public: payload.isPublic })
    closeAnnoPopover()
    paintTextLayerAnnotations()
  } catch (e: any) {
    console.error('Failed to update annotation', e)
    alert(e.response?.data?.detail || e.message || 'Failed to update annotation')
  }
}

const onAnnoDelete = async (id: number) => {
  try {
    await annotationsApi.remove(id)
    closeAnnoPopover()
    paintTextLayerAnnotations()
  } catch (e: any) {
    console.error('Failed to delete annotation', e)
    alert(e.response?.data?.detail || e.message || 'Failed to delete annotation')
  }
}

const onAnnoJump = (a: Annotation) => {
  const anchor = a.anchor as { type: string; page: number }
  if (anchor.type !== 'pdf') return
  const onCurrentSpread = anchor.page === leftPage.value || anchor.page === rightPage.value
  if (onCurrentSpread) {
    annoExisting.value = a
    annoPending.value = null
    return
  }
  renderPage(anchor.page).then(() => {
    annoExisting.value = a
    annoPending.value = null
  })
}

watch(() => annotationsApi.visible.value, () => {
  paintTextLayerAnnotations()
})

const saveProgress = () => {
  if (!hashId.value) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(async () => {
    try {
      await api.post('/progress', {
        hash_id: hashId.value,
        location: JSON.stringify({
          page: currentPage.value,
          fitMode: fitMode.value,
          scale: customScale.value,
          isDoublePage: isDoublePage.value,
          oddOnRight: oddOnRight.value,
          textSelectEnabled: textSelectEnabled.value,
        }),
        percent: totalPages.value > 0
          ? Math.min(1, currentPage.value / totalPages.value)
          : null,
      })
    } catch (e) {
      console.error('Failed to save progress', e)
    }
  }, 500)
}

const loadProgress = async (): Promise<{ page: number; fitMode?: 'width' | 'height' | 'custom'; scale?: number; isDoublePage?: boolean; oddOnRight?: boolean; textSelectEnabled?: boolean } | null> => {
  if (!hashId.value) return null
  try {
    const res = await api.get(`/progress/${encodeURIComponent(hashId.value)}`)
    try {
      const data = JSON.parse(res.data.location)
      if (typeof data.page === 'number') return data
      const n = parseInt(String(data.page ?? data))
      return Number.isFinite(n) ? { page: n } : null
    } catch {
      const n = parseInt(res.data.location)
      return Number.isFinite(n) ? { page: n } : null
    }
  } catch {
    return null
  }
}

const computeFitScale = (page: PDFPageProxy): number => {
  if (!container.value) return 1
  const base = page.getViewport({ scale: 1 })
  // Match the container's padding (p-2 lg:p-4) and the gap-2 between pages.
  const padX = 32
  const padY = 32
  const gap = isDoublePage.value ? 8 : 0
  // Use offsetWidth/offsetHeight (the outer box, unaffected by an internal
  // scrollbar toggling) and reserve a constant for a possible perpendicular
  // scrollbar. Otherwise fit-height oscillates when the scaled page width
  // sits right on the horizontal-scrollbar threshold: the scrollbar appears,
  // clientHeight shrinks, the recomputed scale drops, scrollbar disappears,
  // clientHeight grows back, loop.
  const scrollbar = 18
  if (fitMode.value === 'height') {
    const h = container.value.offsetHeight - padY - scrollbar
    return Math.max(0.05, h / base.height)
  }
  // fit-width
  const usableW = container.value.offsetWidth - padX - gap - scrollbar
  const perPage = isDoublePage.value ? usableW / 2 : usableW
  return Math.max(0.05, perPage / base.width)
}

const renderOne = async (page: PDFPageProxy, canvasEl: HTMLCanvasElement, effective: number): Promise<{ task: RenderTask; cssViewport: PageViewport } | null> => {
  const dpr = window.devicePixelRatio || 1
  const viewport = page.getViewport({ scale: effective * dpr })
  const cssViewport = page.getViewport({ scale: effective })
  const ctx = canvasEl.getContext('2d')
  if (!ctx) return null
  canvasEl.width = Math.floor(viewport.width)
  canvasEl.height = Math.floor(viewport.height)
  canvasEl.style.width = `${Math.floor(cssViewport.width)}px`
  canvasEl.style.height = `${Math.floor(cssViewport.height)}px`
  const task = page.render({ canvasContext: ctx, viewport, canvas: canvasEl })
  return { task, cssViewport }
}

// Build the transparent, selectable text overlay aligned to the canvas. Only
// runs for typeset PDFs — scanned/facsimile PDFs have no extractable text so
// the layer renders empty (no spans, nothing selectable), which is correct.
const renderTextLayerFor = async (
  page: PDFPageProxy,
  containerEl: HTMLElement,
  cssViewport: PageViewport,
) => {
  containerEl.innerHTML = ''
  // pdf.js v5's TextLayer reads `--total-scale-factor` (not `--scale-factor`,
  // which was the v3/v4 name) on the container; every span's font-size is
  // written as `calc(var(--text-scale-factor) * var(--font-height))`. The
  // constructor also calls setLayerDimensions internally, which overwrites
  // container width/height with calc(... * --total-scale-factor), so we don't
  // set them ourselves.
  containerEl.style.setProperty('--total-scale-factor', String(cssViewport.scale))
  const layer = new pdfjsLib.TextLayer({
    textContentSource: page.streamTextContent(),
    container: containerEl,
    viewport: cssViewport,
  })
  activeTextLayers.push(layer)
  try {
    await layer.render()
  } catch (e: any) {
    if (e?.name !== 'AbortException') throw e
  }
}

// Size a canvas to match refPage's dimensions and leave it blank white — used
// for the empty left half of the lone cover spread in odd-right mode.
const renderBlank = (canvasEl: HTMLCanvasElement, refPage: PDFPageProxy, effective: number) => {
  const dpr = window.devicePixelRatio || 1
  const viewport = refPage.getViewport({ scale: effective * dpr })
  const cssViewport = refPage.getViewport({ scale: effective })
  const ctx = canvasEl.getContext('2d')
  if (!ctx) return
  canvasEl.width = Math.floor(viewport.width)
  canvasEl.height = Math.floor(viewport.height)
  canvasEl.style.width = `${Math.floor(cssViewport.width)}px`
  canvasEl.style.height = `${Math.floor(cssViewport.height)}px`
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, canvasEl.width, canvasEl.height)
}

const cancelTasks = () => {
  for (const t of activeTasks) {
    try { t.cancel() } catch { /* already done */ }
  }
  activeTasks = []
  for (const l of activeTextLayers) {
    try { l.cancel() } catch { /* already done */ }
  }
  activeTextLayers = []
  if (textLayer.value) textLayer.value.innerHTML = ''
  if (textLayer2.value) textLayer2.value.innerHTML = ''
}

// Snap an arbitrary page number to the spread that contains it. A spread may
// have a null left slot (the lone cover in odd-right mode) or a null right
// slot (a lone last page).
const pageSpread = (p: number): { left: number | null; right: number | null } => {
  if (!isDoublePage.value) return { left: p, right: null }
  if (oddOnRight.value) {
    if (p <= 1) return { left: null, right: 1 }
    const base = p % 2 === 0 ? p : p - 1
    return { left: base, right: base + 1 <= totalPages.value ? base + 1 : null }
  }
  const base = p % 2 === 1 ? p : p - 1
  return { left: base, right: base + 1 <= totalPages.value ? base + 1 : null }
}

const renderPage = async (n: number) => {
  if (!pdfDoc || !canvas.value) return
  if (n < 1 || n > totalPages.value) return
  cancelTasks()
  loadingPage.value = true
  try {
    const sp = pageSpread(n)
    const anchor = sp.left ?? sp.right ?? n
    const scalePage = await pdfDoc.getPage(anchor)
    const effective = fitMode.value === 'custom' ? customScale.value : computeFitScale(scalePage)
    renderedScale.value = effective

    let pageL: PDFPageProxy | null = null
    let pageR: PDFPageProxy | null = null
    let cssL: PageViewport | null = null
    let cssR: PageViewport | null = null

    if (sp.left !== null) {
      pageL = sp.left === anchor ? scalePage : await pdfDoc.getPage(sp.left)
      const r = await renderOne(pageL, canvas.value, effective)
      if (r) { activeTasks.push(r.task); cssL = r.cssViewport }
    } else {
      // Odd-right cover: blank left half so the cover page sits on the right.
      renderBlank(canvas.value, scalePage, effective)
    }
    if (sp.right !== null && canvas2.value) {
      pageR = sp.right === anchor ? scalePage : await pdfDoc.getPage(sp.right)
      const r = await renderOne(pageR, canvas2.value, effective)
      if (r) { activeTasks.push(r.task); cssR = r.cssViewport }
    }

    await Promise.all(activeTasks.map(t => t.promise))

    if (textSelectEnabled.value) {
      const jobs: Promise<unknown>[] = []
      if (pageL && cssL && textLayer.value) {
        jobs.push(renderTextLayerFor(pageL, textLayer.value, cssL))
      }
      if (pageR && cssR && textLayer2.value) {
        jobs.push(renderTextLayerFor(pageR, textLayer2.value, cssR))
      }
      await Promise.all(jobs)
    }

    leftPage.value = sp.left
    rightPage.value = sp.right
    currentPage.value = anchor
    saveProgress()
    paintTextLayerAnnotations()
    updateCanPan()
  } catch (e: any) {
    if (e?.name !== 'RenderingCancelledException') {
      error.value = e?.message || 'Render failed'
    }
  } finally {
    loadingPage.value = false
  }
}

// Turning the page resets the viewport: Next lands at the top of the new
// page, Prev at the bottom — so the reader continues from where their eye
// already was, not from where the previous page happened to be scrolled.
const nextPage = async () => {
  const last = rightPage.value ?? leftPage.value ?? currentPage.value
  if (last < totalPages.value) {
    await renderPage(last + 1)
    if (container.value) container.value.scrollTop = 0
  }
}
const prevPage = async () => {
  const first = leftPage.value ?? rightPage.value ?? currentPage.value
  if (first > 1) {
    await renderPage(first - 1)
    if (container.value) container.value.scrollTop = container.value.scrollHeight
  }
}
const toggleViewMode = () => {
  isDoublePage.value = !isDoublePage.value
  renderPage(currentPage.value)
}
const toggleOddSide = () => {
  if (!isDoublePage.value) return
  oddOnRight.value = !oddOnRight.value
  renderPage(currentPage.value)
}
const toggleTextSelect = () => {
  textSelectEnabled.value = !textSelectEnabled.value
  // Re-render so the text layer is built (or cleanly torn down) at the
  // current scale. The canvas itself doesn't need to repaint, but reusing
  // renderPage keeps page-spread / blank-cover bookkeeping in one place.
  renderPage(currentPage.value)
}

const goToPage = (e: Event) => {
  const target = e.target as HTMLInputElement
  let p = parseInt(target.value)
  if (!Number.isFinite(p)) return
  if (p < 1) p = 1
  if (p > totalPages.value) p = totalPages.value
  target.value = String(p)
  renderPage(p)
}

// Step the zoom by 5 percentage points, snapping to a 5 % grid so the user
// always lands on round values (95, 100, 105, …) regardless of where they
// started. Additive — not multiplicative — so the step size is independent
// of the current zoom and the window size.
const zoomIn = () => {
  const cur = fitMode.value === 'custom' ? customScale.value : renderedScale.value
  customScale.value = Math.min(100, (Math.floor(cur * 20) + 1) / 20)
  fitMode.value = 'custom'
  renderPage(currentPage.value)
}
const zoomOut = () => {
  const cur = fitMode.value === 'custom' ? customScale.value : renderedScale.value
  customScale.value = Math.max(0.001, (Math.ceil(cur * 20) - 1) / 20)
  fitMode.value = 'custom'
  renderPage(currentPage.value)
}
const fitWidth = () => {
  fitMode.value = 'width'
  renderPage(currentPage.value)
}
const fitHeight = () => {
  fitMode.value = 'height'
  renderPage(currentPage.value)
}

const buildOutline = async (items: any[]): Promise<PdfOutlineNode[]> => {
  if (!pdfDoc) return []
  const out: PdfOutlineNode[] = []
  for (const it of items) {
    let page: number | null = null
    try {
      let dest = it.dest
      if (typeof dest === 'string') dest = await pdfDoc.getDestination(dest)
      if (dest && Array.isArray(dest) && dest[0]) {
        const idx = await pdfDoc.getPageIndex(dest[0])
        page = idx + 1
      }
    } catch { /* unresolvable entry; show without link */ }
    const children = it.items?.length ? await buildOutline(it.items) : []
    out.push({ title: it.title || '—', page, children })
  }
  return out
}

const onTocNavigate = (page: number) => {
  if (!page || page < 1 || page > totalPages.value) return
  renderPage(page)
  if (immersive.value) tocOpen.value = false
}

const toggleImmersive = () => { immersive.value = !immersive.value }

// PgDn/PgUp scroll the viewport within the current page; only when already
// at the bottom/top does the keypress flip to the next/prev page (which then
// lands at the top/bottom of the new page via nextPage/prevPage).
const onKeyDown = (e: KeyboardEvent) => {
  if (!immersive.value) return
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
  // Don't flip the page out from under an active text selection — Escape
  // still passes through so the user can leave immersive mode.
  if (e.key !== 'Escape' && (window.getSelection()?.toString().length ?? 0) > 0) return
  if (e.key === 'Escape') {
    e.preventDefault()
    immersive.value = false
  } else if (e.key === 'PageDown') {
    e.preventDefault()
    const c = container.value
    if (c && c.scrollTop + c.clientHeight < c.scrollHeight - 1) {
      c.scrollBy({ top: c.clientHeight - 40 })
    } else {
      nextPage()
    }
  } else if (e.key === 'PageUp') {
    e.preventDefault()
    const c = container.value
    if (c && c.scrollTop > 0) {
      c.scrollBy({ top: -(c.clientHeight - 40) })
    } else {
      prevPage()
    }
  } else if (e.key === 'Home') {
    e.preventDefault()
    if (container.value) container.value.scrollTop = 0
  } else if (e.key === 'End') {
    e.preventDefault()
    if (container.value) container.value.scrollTop = container.value.scrollHeight
  } else if (e.key === 'd') {
    e.preventDefault()
    toggleViewMode()
  } else if (e.key === 'o') {
    e.preventDefault()
    toggleOddSide()
  }
}

watch(immersive, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
  document.documentElement.style.overflow = v ? 'hidden' : ''
  if (v) tocOpen.value = false
})

const initPdf = async () => {
  loading.value = true
  error.value = ''
  toc.value = []
  try {
    const res = await api.get(viewerUrls(props.source).file, {
      responseType: 'arraybuffer',
    })
    const data = new Uint8Array(res.data as ArrayBuffer)
    // CMaps, Foxit standard-font substitutes, and the JBig2/JPEG2000/QCMS
    // wasm decoders are copied into dist/pdfjs/ at build time (and served
    // from node_modules in dev) by the `pdfjs-assets` plugin in
    // vite.config.ts. Without these URLs, scans compressed with JBig2
    // (Adobe Paper-Capture facsimile PDFs are the obvious case) silently
    // fail to decode and the page renders blank.
    const base = import.meta.env.BASE_URL
    const loadingTask = pdfjsLib.getDocument({
      data,
      cMapUrl: `${base}pdfjs/cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${base}pdfjs/standard_fonts/`,
      wasmUrl: `${base}pdfjs/wasm/`,
    })
    pdfDoc = await loadingTask.promise
    totalPages.value = pdfDoc.numPages

    try {
      const outline = await pdfDoc.getOutline()
      if (outline) toc.value = await buildOutline(outline)
    } catch (e) {
      // Outline is best-effort; some PDFs have none or have broken refs.
      console.error('Failed to load PDF outline', e)
    }

    const saved = await loadProgress()
    if (saved?.fitMode === 'custom' && typeof saved.scale === 'number') {
      fitMode.value = 'custom'
      customScale.value = saved.scale
    } else if (saved?.fitMode === 'height') {
      fitMode.value = 'height'
    } else if (saved?.fitMode === 'width') {
      fitMode.value = 'width'
    }
    if (typeof saved?.isDoublePage === 'boolean') {
      isDoublePage.value = saved.isDoublePage
    }
    if (typeof saved?.oddOnRight === 'boolean') {
      oddOnRight.value = saved.oddOnRight
    }
    if (typeof saved?.textSelectEnabled === 'boolean') {
      textSelectEnabled.value = saved.textSelectEnabled
    }
    const startPage = saved && saved.page >= 1 && saved.page <= totalPages.value ? saved.page : 1
    await renderPage(startPage)
    await annotationsApi.load()
    paintTextLayerAnnotations()
  } catch (e: any) {
    error.value = e?.message || 'Failed to load PDF'
  } finally {
    loading.value = false
  }
}

const destroy = () => {
  cancelTasks()
  if (pdfDoc) {
    pdfDoc.destroy().catch(() => {})
    pdfDoc = null
  }
  resizeObs?.disconnect()
  resizeObs = null
  if (resizeTimer) { clearTimeout(resizeTimer); resizeTimer = null }
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
}

onMounted(async () => {
  window.addEventListener('keydown', onKeyDown)
  await initPdf()
  if (container.value) {
    let lastW = container.value.clientWidth
    let lastH = container.value.clientHeight
    resizeObs = new ResizeObserver(() => {
      if (!container.value) return
      const w = container.value.clientWidth
      const h = container.value.clientHeight
      if (w === lastW && h === lastH) return
      lastW = w
      lastH = h
      // In custom zoom the user picked an explicit scale and shouldn't be
      // overridden on resize. fit-width/height should re-fit.
      if (fitMode.value === 'custom') return
      if (resizeTimer) clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => renderPage(currentPage.value), 150)
    })
    resizeObs.observe(container.value)
  }
})

watch(() => sourceLoadKey(props.source), async () => {
  destroy()
  await initPdf()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  document.body.style.overflow = ''
  document.documentElement.style.overflow = ''
  destroy()
})
</script>

<template>
  <div
    :class="[
      'pdf-viewer flex flex-col items-stretch bg-gray-100 dark:bg-gray-800 w-full overscroll-contain',
      immersive
        ? 'fixed inset-0 z-50 h-dvh rounded-none'
        : 'relative h-[80vh] rounded-lg shadow'
    ]"
  >
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-black/50 z-30 rounded-lg">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
    </div>
    <div v-else-if="error" class="absolute inset-0 flex items-center justify-center bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded-lg z-30 p-4 text-center">
      {{ error }}
    </div>

    <!-- Toolbar (hidden in immersive) -->
    <div v-if="!immersive" class="w-full flex flex-wrap items-center justify-between p-2 sm:p-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg shadow-sm gap-2 z-20">
      <div class="flex items-center gap-1 sm:gap-2">
        <button
          @click="prevPage"
          :disabled="(leftPage ?? rightPage ?? currentPage) <= 1 || loadingPage"
          :title="t('djvu.previous')"
          class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronLeftIcon class="h-5 w-5" />
        </button>
        <button
          @click="nextPage"
          :disabled="(rightPage ?? leftPage ?? currentPage) >= totalPages || loadingPage"
          :title="t('djvu.next')"
          class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronRightIcon class="h-5 w-5" />
        </button>
      </div>

      <div class="flex items-center space-x-2 text-gray-700 dark:text-gray-300 text-sm md:text-base font-medium">
        <span>{{ t('djvu.page') }}</span>
        <input
          type="number"
          :value="currentPage"
          @change="goToPage"
          min="1"
          :max="totalPages"
          :disabled="loadingPage"
          class="w-16 px-2 py-1 text-center border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
        />
        <span v-if="rightPage !== null && leftPage !== null">- {{ rightPage }}</span>
        <span>{{ t('djvu.of') }} {{ totalPages }}</span>
      </div>

      <div class="flex flex-wrap items-center justify-end gap-0.5 sm:gap-2">
        <button @click="zoomOut" :disabled="loadingPage" :title="t('app.zoom_out')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors disabled:opacity-50">
          <MagnifyingGlassMinusIcon class="h-5 w-5" />
        </button>
        <button
          @click="fitWidth"
          :disabled="loadingPage"
          :title="t('app.fit_width')"
          :class="[
            'px-2 py-1 rounded transition-colors disabled:opacity-50',
            fitMode === 'width'
              ? 'bg-blue-500 text-white hover:bg-blue-600'
              : 'bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200'
          ]"
        >
          <ArrowsRightLeftIcon class="h-5 w-5" />
        </button>
        <button
          @click="fitHeight"
          :disabled="loadingPage"
          :title="t('app.fit_height')"
          :class="[
            'px-2 py-1 rounded transition-colors disabled:opacity-50',
            fitMode === 'height'
              ? 'bg-blue-500 text-white hover:bg-blue-600'
              : 'bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200'
          ]"
        >
          <ArrowsUpDownIcon class="h-5 w-5" />
        </button>
        <div class="flex items-center gap-0.5 shrink-0">
          <input
            v-model="zoomInput"
            type="text"
            inputmode="decimal"
            :title="t('app.zoom_set')"
            :disabled="loadingPage"
            @focus="($event.target as HTMLInputElement).select()"
            @keydown.enter.prevent="onZoomEnter"
            @keydown.escape.prevent="cancelZoomInput"
            @blur="commitZoomInput"
            class="w-14 px-1 text-center text-sm tabular-nums bg-transparent rounded border border-transparent hover:border-gray-300 dark:hover:border-gray-600 focus:border-blue-500 focus:bg-white dark:focus:bg-gray-800 outline-none text-gray-600 dark:text-gray-400 disabled:opacity-50"
          />
          <span class="text-sm text-gray-600 dark:text-gray-400 select-none">%</span>
        </div>
        <button @click="zoomIn" :disabled="loadingPage" :title="t('app.zoom_in')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors disabled:opacity-50">
          <MagnifyingGlassPlusIcon class="h-5 w-5" />
        </button>
        <button
          @click="toggleViewMode"
          :disabled="loadingPage"
          :title="isDoublePage ? t('djvu.singlePage') : t('djvu.twoPages')"
          class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors disabled:opacity-50"
        >
          <DocumentIcon v-if="isDoublePage" class="h-5 w-5" />
          <BookOpenIcon v-else class="h-5 w-5" />
        </button>
        <button
          @click="toggleOddSide"
          :disabled="!isDoublePage || loadingPage"
          :title="oddOnRight ? t('djvu.oddPagesRight') : t('djvu.oddPagesLeft')"
          class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors disabled:opacity-50"
        >
          <ArrowLongRightIcon v-if="oddOnRight" class="h-5 w-5" />
          <ArrowLongLeftIcon v-else class="h-5 w-5" />
        </button>
        <button
          @click="toggleTextSelect"
          :disabled="loadingPage"
          :title="textSelectEnabled ? t('app.text_select_disable') : t('app.text_select_enable')"
          :class="[
            'px-2 py-1 rounded transition-colors disabled:opacity-50',
            textSelectEnabled
              ? 'bg-blue-500 text-white hover:bg-blue-600'
              : 'bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200'
          ]"
        >
          <Bars3BottomLeftIcon class="h-5 w-5" />
        </button>
        <AnnotationVisibilityToggle v-model="annotationsApi.visibility.value" />
        <button @click="toggleImmersive" :title="t('app.immersive_enter')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors">
          <ArrowsPointingOutIcon class="h-5 w-5" />
        </button>
      </div>
    </div>

    <!-- TOC sidebar + canvas -->
    <div class="flex flex-row flex-grow min-h-0 overflow-hidden" :class="immersive ? '' : 'rounded-b-lg'">
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
          <PdfTocNode
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

      <div
        ref="container"
        class="relative flex-grow min-w-0 bg-gray-200 dark:bg-gray-900 overflow-auto p-2 lg:p-4"
        :class="canPan ? (isPanning ? 'cursor-grabbing' : 'cursor-grab') : ''"
        @mousedown="onPanMouseDown"
      >
        <div v-if="loadingPage" class="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-black/50 z-10 pointer-events-none">
          <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
        </div>
        <!-- w-fit + mx-auto: centers when content fits, clamps left margin to 0
             when content overflows so both pages are reachable by horizontal
             scroll (instead of being centered off-screen on both sides). -->
        <div class="flex flex-row items-start gap-2 w-fit mx-auto">
          <div class="page-wrap relative shadow-md bg-white">
            <canvas ref="canvas"></canvas>
            <div ref="textLayer" class="textLayer" v-show="textSelectEnabled" @mouseup="onTextLayerMouseUp($event, 'left')"></div>
          </div>
          <div
            class="page-wrap relative shadow-md bg-white"
            v-show="rightPage !== null"
          >
            <canvas ref="canvas2"></canvas>
            <div ref="textLayer2" class="textLayer" v-show="textSelectEnabled" @mouseup="onTextLayerMouseUp($event, 'right')"></div>
          </div>
        </div>
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
      </div>
    </div>

    <!-- Immersive floating controls. Sit outside the scrolling container so
         they remain pinned to the viewport when the page is taller than the
         viewport and the user scrolls. -->
    <template v-if="immersive">
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
        <!-- pb-16: clear the floating prev-page button so the last TOC entries
             (and their expand arrows) stay tappable. -->
        <nav class="flex-grow overflow-auto p-1 pb-16 text-gray-800 dark:text-gray-200">
          <p v-if="!toc.length" class="px-2 py-2 text-xs text-gray-500 dark:text-gray-400">{{ t('app.toc_empty') }}</p>
          <PdfTocNode
            v-for="(entry, i) in toc"
            :key="i"
            :entry="entry"
            :level="0"
            @navigate="onTocNavigate"
          />
        </nav>
      </aside>

      <button
        v-if="!tocOpen"
        @click="tocOpen = true"
        :title="t('app.toc_expand')"
        class="absolute top-2 left-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white"
      >
        <Bars3Icon class="h-5 w-5" />
      </button>

      <button
        @click="toggleImmersive"
        :title="t('app.immersive_exit')"
        class="absolute top-2 right-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white"
      >
        <ArrowsPointingInIcon class="h-5 w-5" />
      </button>

      <button
        @click="prevPage"
        :disabled="(leftPage ?? rightPage ?? currentPage) <= 1 || loadingPage"
        :title="t('djvu.previous')"
        class="absolute bottom-3 left-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
      >
        <ChevronLeftIcon class="h-6 w-6" />
      </button>

      <button
        @click="nextPage"
        :disabled="(rightPage ?? leftPage ?? currentPage) >= totalPages || loadingPage"
        :title="t('djvu.next')"
        class="absolute bottom-3 right-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
      >
        <ChevronRightIcon class="h-6 w-6" />
      </button>

      <div class="absolute bottom-3 left-1/2 -translate-x-1/2 z-40 px-3 py-1 rounded-full bg-black/15 text-white/80 text-sm select-none pointer-events-none">
        {{ currentPage }}<span v-if="rightPage !== null && leftPage !== null">–{{ rightPage }}</span> / {{ totalPages }}
      </div>
    </template>
  </div>
</template>

<style scoped>
input[type=number]::-webkit-inner-spin-button,
input[type=number]::-webkit-outer-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
input[type=number] {
  -moz-appearance: textfield;
}

/* Kill the inline-block descender gap so the .textLayer overlay sits exactly
   on top of the canvas pixels (otherwise spans drift a few px downward). */
.page-wrap { line-height: 0; }

/* Minimal pdf.js v5 text-layer rules — cribbed from
   pdfjs-dist/web/pdf_viewer.css (~885-1027) and trimmed to what selection
   needs. The CSS-var cascade here is load-bearing: each span gets
   `font-size: calc(var(--text-scale-factor) * var(--font-height))` and
   `transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv))`
   via pdf.js's emitted inline styles, and those formulas pull from the
   variables defined here. Drop any of these rules and spans collapse to
   ~1px slivers (the selection bug we hit first). `:deep()` is required
   because pdf.js writes the spans dynamically, so Vue's scoped data
   attribute is not present on them. */
.textLayer {
  position: absolute;
  inset: 0;
  overflow: hidden;
  opacity: 1;
  line-height: 1;
  text-size-adjust: none;
  forced-color-adjust: none;
  transform-origin: 0 0;
  caret-color: CanvasText;
  cursor: text;
  z-index: 2;

  --min-font-size: 1;
  --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
  --min-font-size-inv: calc(1 / var(--min-font-size));
}
.textLayer :deep(span),
.textLayer :deep(br) {
  color: transparent;
  position: absolute;
  white-space: pre;
  cursor: text;
  transform-origin: 0% 0%;
}
.textLayer :deep(> :not(.markedContent)),
.textLayer :deep(.markedContent span:not(.markedContent)) {
  z-index: 1;

  --font-height: 0;
  font-size: calc(var(--text-scale-factor) * var(--font-height));

  --scale-x: 1;
  --rotate: 0deg;
  transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
}
.textLayer :deep(.markedContent) {
  display: contents;
}
.textLayer :deep(::selection) {
  background: rgba(0, 100, 255, 0.3);
}
.textLayer :deep(br::selection) {
  background: transparent;
}
.textLayer :deep(.endOfContent) {
  display: block;
  position: absolute;
  inset: 100% 0 0;
  z-index: -1;
  cursor: default;
  user-select: none;
}
</style>
