<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist'
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
  ArrowsRightLeftIcon,
  ArrowsUpDownIcon,
  MagnifyingGlassPlusIcon,
  MagnifyingGlassMinusIcon,
} from '@heroicons/vue/24/outline'
import PdfTocNode, { type PdfOutlineNode } from './PdfTocNode.vue'
import { viewerUrls, sourceHashId, type ViewerSource } from './viewerSource'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{ source: ViewerSource }>()
const hashId = computed(() => sourceHashId(props.source))

const canvas = ref<HTMLCanvasElement | null>(null)
const canvas2 = ref<HTMLCanvasElement | null>(null)
const container = ref<HTMLElement | null>(null)

let pdfDoc: PDFDocumentProxy | null = null
let activeTasks: RenderTask[] = []
let resizeObs: ResizeObserver | null = null
let resizeTimer: ReturnType<typeof setTimeout> | null = null
let saveTimer: ReturnType<typeof setTimeout> | null = null

const loading = ref(true)
const loadingPage = ref(false)
const error = ref('')
const totalPages = ref(0)
const currentPage = ref(1)
const renderedScale = ref(1)
const isDoublePage = ref(false)

// 'width' / 'height': re-fit to container on every render (and on resize).
// 'custom': user picked a zoom level; persist verbatim.
const fitMode = ref<'width' | 'height' | 'custom'>('width')
const customScale = ref(1)

const toc = ref<PdfOutlineNode[]>([])
const tocOpen = ref(false)
const immersive = ref(false)

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
        }),
      })
    } catch (e) {
      console.error('Failed to save progress', e)
    }
  }, 500)
}

const loadProgress = async (): Promise<{ page: number; fitMode?: 'width' | 'height' | 'custom'; scale?: number; isDoublePage?: boolean } | null> => {
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
  if (fitMode.value === 'height') {
    const h = container.value.clientHeight - padY
    return Math.max(0.1, h / base.height)
  }
  // fit-width
  const usableW = container.value.clientWidth - padX - gap
  const perPage = isDoublePage.value ? usableW / 2 : usableW
  return Math.max(0.1, perPage / base.width)
}

const renderOne = async (page: PDFPageProxy, canvasEl: HTMLCanvasElement, effective: number): Promise<RenderTask | null> => {
  const dpr = window.devicePixelRatio || 1
  const viewport = page.getViewport({ scale: effective * dpr })
  const cssViewport = page.getViewport({ scale: effective })
  const ctx = canvasEl.getContext('2d')
  if (!ctx) return null
  canvasEl.width = Math.floor(viewport.width)
  canvasEl.height = Math.floor(viewport.height)
  canvasEl.style.width = `${Math.floor(cssViewport.width)}px`
  canvasEl.style.height = `${Math.floor(cssViewport.height)}px`
  return page.render({ canvasContext: ctx, viewport, canvas: canvasEl })
}

const cancelTasks = () => {
  for (const t of activeTasks) {
    try { t.cancel() } catch { /* already done */ }
  }
  activeTasks = []
}

const renderPage = async (n: number) => {
  if (!pdfDoc || !canvas.value) return
  if (n < 1 || n > totalPages.value) return
  cancelTasks()
  loadingPage.value = true
  try {
    const page = await pdfDoc.getPage(n)
    const effective = fitMode.value === 'custom' ? customScale.value : computeFitScale(page)
    renderedScale.value = effective

    const t1 = await renderOne(page, canvas.value, effective)
    if (t1) activeTasks.push(t1)

    let t2: RenderTask | null = null
    if (isDoublePage.value && n + 1 <= totalPages.value && canvas2.value) {
      const page2 = await pdfDoc.getPage(n + 1)
      t2 = await renderOne(page2, canvas2.value, effective)
      if (t2) activeTasks.push(t2)
    }

    await Promise.all(activeTasks.map(t => t.promise))
    currentPage.value = n
    saveProgress()
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
  const step = isDoublePage.value ? 2 : 1
  if (currentPage.value < totalPages.value) {
    let next = currentPage.value + step
    if (next > totalPages.value) next = totalPages.value
    await renderPage(next)
    if (container.value) container.value.scrollTop = 0
  }
}
const prevPage = async () => {
  const step = isDoublePage.value ? 2 : 1
  if (currentPage.value > 1) {
    let prev = currentPage.value - step
    if (prev < 1) prev = 1
    await renderPage(prev)
    if (container.value) container.value.scrollTop = container.value.scrollHeight
  }
}
const toggleViewMode = () => {
  isDoublePage.value = !isDoublePage.value
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

const zoomIn = () => {
  customScale.value = Math.min(5, (fitMode.value === 'custom' ? customScale.value : renderedScale.value) * 1.2)
  fitMode.value = 'custom'
  renderPage(currentPage.value)
}
const zoomOut = () => {
  customScale.value = Math.max(0.2, (fitMode.value === 'custom' ? customScale.value : renderedScale.value) / 1.2)
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
    const loadingTask = pdfjsLib.getDocument({ data })
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
    const startPage = saved && saved.page >= 1 && saved.page <= totalPages.value ? saved.page : 1
    await renderPage(startPage)
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

watch(() => props.source, async () => {
  destroy()
  await initPdf()
}, { deep: true })

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
          :disabled="currentPage <= 1 || loadingPage"
          :title="t('djvu.previous')"
          class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronLeftIcon class="h-5 w-5" />
        </button>
        <button
          @click="nextPage"
          :disabled="(isDoublePage ? currentPage >= totalPages - 1 && totalPages > 1 : currentPage >= totalPages) || loadingPage"
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
        <span v-if="isDoublePage && currentPage < totalPages">- {{ currentPage + 1 }}</span>
        <span>{{ t('djvu.of') }} {{ totalPages }}</span>
      </div>

      <div class="flex items-center gap-1 sm:gap-2 shrink-0">
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
        <span class="px-1 text-sm text-gray-600 dark:text-gray-400 tabular-nums select-none">{{ Math.round(renderedScale * 100) }}%</span>
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
      </aside>

      <div ref="container" class="relative flex-grow min-w-0 bg-gray-200 dark:bg-gray-900 overflow-auto p-2 lg:p-4">
        <div v-if="loadingPage" class="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-black/50 z-10 pointer-events-none">
          <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
        </div>
        <!-- w-fit + mx-auto: centers when content fits, clamps left margin to 0
             when content overflows so both pages are reachable by horizontal
             scroll (instead of being centered off-screen on both sides). -->
        <div class="flex flex-row items-start gap-2 w-fit mx-auto">
          <canvas ref="canvas" class="shadow-md bg-white"></canvas>
          <canvas
            ref="canvas2"
            v-show="isDoublePage && currentPage < totalPages"
            class="shadow-md bg-white"
          ></canvas>
        </div>
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
        <nav class="flex-grow overflow-auto p-1 text-gray-800 dark:text-gray-200">
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
        :disabled="currentPage <= 1 || loadingPage"
        :title="t('djvu.previous')"
        class="absolute bottom-3 left-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
      >
        <ChevronLeftIcon class="h-6 w-6" />
      </button>

      <button
        @click="nextPage"
        :disabled="(isDoublePage ? currentPage >= totalPages - 1 && totalPages > 1 : currentPage >= totalPages) || loadingPage"
        :title="t('djvu.next')"
        class="absolute bottom-3 right-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
      >
        <ChevronRightIcon class="h-6 w-6" />
      </button>

      <div class="absolute bottom-3 left-1/2 -translate-x-1/2 z-40 px-3 py-1 rounded-full bg-black/15 text-white/80 text-sm select-none pointer-events-none">
        {{ currentPage }}<span v-if="isDoublePage && currentPage < totalPages">–{{ currentPage + 1 }}</span> / {{ totalPages }}
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
</style>
