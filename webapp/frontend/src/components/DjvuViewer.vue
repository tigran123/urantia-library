<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import api from '../api'
import { useI18n } from 'vue-i18n'
import {
  Bars3Icon,
  ChevronDoubleLeftIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  BookOpenIcon,
  DocumentIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
  ArrowLongLeftIcon,
  ArrowLongRightIcon,
  ArrowsRightLeftIcon,
  ArrowsUpDownIcon,
  MagnifyingGlassPlusIcon,
  MagnifyingGlassMinusIcon,
} from '@heroicons/vue/24/outline'
import DjvuTocNode, { type DjvuOutlineNode } from './DjvuTocNode.vue'
import { viewerUrls, viewerParams, sourceHashId, type ViewerSource } from './viewerSource'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{ source: ViewerSource }>()
const hashId = computed(() => sourceHashId(props.source))

const totalPages = ref(0)
const currentPage = ref(1)
const loadingMetadata = ref(true)
const loadingPage = ref(false)
const error = ref('')

const imageUrl = ref('')
const imageUrl2 = ref('')
const isDoublePage = ref(false)
// Whether odd page numbers fall on the right (evince default): spreads run
// [1] [2,3] [4,5]…. When false, odd pages fall on the left: [1,2] [3,4]….
const oddOnRight = ref(true)
// The two pages of the spread currently on screen; either may be null (the
// lone cover in odd-right mode, or a lone last page).
const leftPage = ref<number | null>(null)
const rightPage = ref<number | null>(null)
const immersive = ref(false)
const container = ref<HTMLElement | null>(null)

// Zoom. 'width'/'height' re-fit the page to the container on every render and
// on resize; 'custom' keeps the scale the user dialled in. Mirrors PdfViewer.
const fitMode = ref<'width' | 'height' | 'custom'>('width')
const customScale = ref(1)
const renderedScale = ref(1)
// Natural pixel size of each page image, and its on-screen size after scaling.
const natL = ref<{ w: number; h: number }>({ w: 0, h: 0 })
const natR = ref<{ w: number; h: number }>({ w: 0, h: 0 })
const dispL = ref<{ w: number; h: number } | null>(null)
const dispR = ref<{ w: number; h: number } | null>(null)
// Fixed-width zoom readout: integer at/above 100%, one decimal below. The
// constant string width matters — a variable-width label reflows the toolbar,
// whose height feeds back into the fit-height scale and oscillates forever.
const zoomPercent = computed(() =>
  renderedScale.value >= 1
    ? Math.round(renderedScale.value * 100).toString()
    : (renderedScale.value * 100).toFixed(1))

let resizeObs: ResizeObserver | null = null
let resizeTimer: ReturnType<typeof setTimeout> | null = null

const toc = ref<DjvuOutlineNode[]>([])
const tocOpen = ref(false)

const toggleImmersive = () => { immersive.value = !immersive.value }

// Lock body scroll while immersive so accidental swipes near the page edges
// don't drift the underlying app, and to ensure the floating controls don't
// sit behind the browser chrome.
watch(immersive, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
  document.documentElement.style.overflow = v ? 'hidden' : ''
  if (v) tocOpen.value = false
})

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
  } else if (e.key === 'd') {
    e.preventDefault()
    toggleViewMode()
  } else if (e.key === 'o') {
    e.preventDefault()
    toggleOddSide()
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  document.body.style.overflow = ''
  document.documentElement.style.overflow = ''
  resizeObs?.disconnect()
  resizeObs = null
  if (resizeTimer) clearTimeout(resizeTimer)
})

const saveProgress = async (page: number) => {
  if (!hashId.value) return
  try {
    await api.post('/progress', {
      hash_id: hashId.value,
      location: JSON.stringify({
        page: page,
        isDoublePage: isDoublePage.value,
        oddOnRight: oddOnRight.value,
        fitMode: fitMode.value,
        scale: customScale.value,
      }),
      percent: totalPages.value > 0
        ? Math.min(1, page / totalPages.value)
        : null,
    })
  } catch (e) {
    console.error('Failed to save progress', e)
  }
}

const loadProgress = async () => {
  if (!hashId.value) return null
  try {
    const res = await api.get(`/progress/${encodeURIComponent(hashId.value)}`)
    try {
      const data = JSON.parse(res.data.location)
      if (data.isDoublePage !== undefined) {
        isDoublePage.value = data.isDoublePage
      }
      if (typeof data.oddOnRight === 'boolean') {
        oddOnRight.value = data.oddOnRight
      }
      if (data.fitMode === 'width' || data.fitMode === 'height' || data.fitMode === 'custom') {
        fitMode.value = data.fitMode
      }
      if (typeof data.scale === 'number') {
        customScale.value = data.scale
      }
      return parseInt(data.page)
    } catch {
      // Fallback for old progress string format
      return parseInt(res.data.location)
    }
  } catch (e: any) {
    return null
  }
}

// Outline is best-effort: a missing or broken outline must never block the
// page from rendering, so this runs fire-and-forget and only logs on failure.
const fetchOutline = async () => {
  try {
    const urls = viewerUrls(props.source)
    const res = await api.get(urls.djvuOutline, { params: viewerParams(props.source) })
    toc.value = res.data.toc || []
  } catch (e) {
    console.error('Failed to load DjVu outline', e)
  }
}

const fetchMetadata = async () => {
  loadingMetadata.value = true
  error.value = ''
  toc.value = []
  try {
    const urls = viewerUrls(props.source)
    const res = await api.get(urls.djvuMeta, { params: viewerParams(props.source) })
    totalPages.value = res.data.total_pages
    if (totalPages.value > 0) {
      fetchOutline()
      const savedPage = await loadProgress()
      await fetchPage(savedPage && savedPage <= totalPages.value ? savedPage : 1)
    }
  } catch (err: any) {
    error.value = err.response?.data?.detail || 'Failed to load DjVu metadata'
  } finally {
    loadingMetadata.value = false
  }
}

const onTocNavigate = (page: number) => {
  if (!page || page < 1 || page > totalPages.value) return
  fetchPage(page)
  if (immersive.value) tocOpen.value = false
}

const fetchPageData = async (page: number) => {
  const urls = viewerUrls(props.source)
  const res = await api.get(urls.djvuPage, {
    params: viewerParams(props.source, { page }),
    responseType: 'blob'
  })
  const blob = new Blob([res.data], { type: 'image/jpeg' })
  return URL.createObjectURL(blob)
}

// Decode an image purely to read its natural pixel size. The blob URL is the
// same one the <img> will use, so the browser keeps it cached — this is a
// second decode of bytes already in memory, not a second network fetch.
const loadImageDims = (url: string): Promise<{ w: number; h: number }> =>
  new Promise((resolve) => {
    if (!url) { resolve({ w: 0, h: 0 }); return }
    const img = new Image()
    img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight })
    img.onerror = () => resolve({ w: 0, h: 0 })
    img.src = url
  })

// Scale that fits the anchor page to the container per the active fit mode.
const computeFitScale = (): number => {
  const anchor = natL.value.w > 0 ? natL.value : natR.value
  if (!container.value || anchor.w === 0 || anchor.h === 0) return 1
  // Match the container padding (p-2 lg:p-4) and the gap-2 between two pages.
  const padX = 32
  const padY = 32
  const gap = isDoublePage.value ? 8 : 0
  if (fitMode.value === 'height') {
    const h = container.value.clientHeight - padY
    return Math.max(0.05, h / anchor.h)
  }
  const usableW = container.value.clientWidth - padX - gap
  const perPage = isDoublePage.value ? usableW / 2 : usableW
  return Math.max(0.05, perPage / anchor.w)
}

// Recompute each page's on-screen size from the current fit mode. Cheap, so
// it runs on every render, on resize, and on every zoom/fit click.
const applyFit = () => {
  const scale = fitMode.value === 'custom' ? customScale.value : computeFitScale()
  renderedScale.value = scale
  dispL.value = natL.value.w ? { w: natL.value.w * scale, h: natL.value.h * scale } : null
  dispR.value = natR.value.w ? { w: natR.value.w * scale, h: natR.value.h * scale } : null
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

const fetchPage = async (page: number) => {
  if (page < 1 || page > totalPages.value) return
  loadingPage.value = true
  error.value = ''

  try {
    const sp = pageSpread(page)
    const [imgL, imgR] = await Promise.all([
      sp.left !== null ? fetchPageData(sp.left) : Promise.resolve(''),
      sp.right !== null ? fetchPageData(sp.right) : Promise.resolve(''),
    ])
    // Decode natural sizes before showing the page so the fit scale is known
    // on the first paint — no flash of an unscaled image.
    const [dimL, dimR] = await Promise.all([loadImageDims(imgL), loadImageDims(imgR)])

    if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
    if (imageUrl2.value) URL.revokeObjectURL(imageUrl2.value)

    natL.value = dimL
    natR.value = dimR
    imageUrl.value = imgL
    imageUrl2.value = imgR
    leftPage.value = sp.left
    rightPage.value = sp.right
    currentPage.value = sp.left ?? sp.right ?? page
    applyFit()
    saveProgress(currentPage.value)
  } catch (err: any) {
    error.value = err.message || 'Failed to load page'
  } finally {
    loadingPage.value = false
  }
}

// Turning the page resets the viewport: Next lands at the top of the new
// page, Prev at the bottom — so the reader's eye position carries over.
const nextPage = async () => {
  const last = rightPage.value ?? leftPage.value ?? currentPage.value
  if (last < totalPages.value) {
    await fetchPage(last + 1)
    await nextTick()
    if (container.value) container.value.scrollTop = 0
  }
}

const prevPage = async () => {
  const first = leftPage.value ?? rightPage.value ?? currentPage.value
  if (first > 1) {
    await fetchPage(first - 1)
    await nextTick()
    if (container.value) container.value.scrollTop = container.value.scrollHeight
  }
}

const goToPage = (event: Event) => {
  const target = event.target as HTMLInputElement
  let p = parseInt(target.value)
  if (isNaN(p)) return
  if (p < 1) p = 1
  if (p > totalPages.value) p = totalPages.value
  target.value = p.toString()
  fetchPage(p)
}

const toggleViewMode = () => {
  isDoublePage.value = !isDoublePage.value
  fetchPage(currentPage.value)
}

const toggleOddSide = () => {
  if (!isDoublePage.value) return
  oddOnRight.value = !oddOnRight.value
  fetchPage(currentPage.value)
}

// Zoom/fit controls only resize the already-loaded page — no refetch.
const zoomIn = () => {
  customScale.value = Math.min(5, (fitMode.value === 'custom' ? customScale.value : renderedScale.value) * 1.2)
  fitMode.value = 'custom'
  applyFit()
  saveProgress(currentPage.value)
}
const zoomOut = () => {
  customScale.value = Math.max(0.05, (fitMode.value === 'custom' ? customScale.value : renderedScale.value) / 1.2)
  fitMode.value = 'custom'
  applyFit()
  saveProgress(currentPage.value)
}
const fitWidth = () => {
  fitMode.value = 'width'
  applyFit()
  saveProgress(currentPage.value)
}
const fitHeight = () => {
  fitMode.value = 'height'
  applyFit()
  saveProgress(currentPage.value)
}

onMounted(() => {
  fetchMetadata()
})

watch(() => props.source, () => {
  fetchMetadata()
}, { deep: true })

// Re-fit on container resize (the viewer has a CSS resize handle). Skipped in
// custom zoom: that scale is the user's explicit choice, not container-driven.
watch(container, (el) => {
  if (!el || resizeObs) return
  resizeObs = new ResizeObserver(() => {
    if (fitMode.value === 'custom') return
    if (resizeTimer) clearTimeout(resizeTimer)
    resizeTimer = setTimeout(applyFit, 150)
  })
  resizeObs.observe(el)
})
</script>

<template>
  <div
    :class="[
      'djvu-viewer flex flex-col items-stretch bg-gray-100 dark:bg-gray-800 w-full overscroll-contain',
      immersive
        ? 'fixed inset-0 z-50 h-dvh rounded-none'
        : 'relative rounded-lg shadow djvu-resizable'
    ]"
  >
    <div v-if="loadingMetadata" class="p-8">
      <div class="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500 mx-auto"></div>
      <p class="mt-4 text-gray-600 dark:text-gray-400">{{ t('djvu.loadingDetails') }}</p>
    </div>
    
    <div v-else-if="error" class="p-8 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/30 rounded w-full text-center">
      {{ error }}
    </div>
    
    <template v-else-if="totalPages > 0">
      <!-- Toolbar (hidden in immersive mode) -->
      <div v-if="!immersive" class="w-full flex flex-wrap items-center justify-between p-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg shadow-sm gap-2">
        <div class="flex items-center space-x-2">
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
          <span class="w-12 shrink-0 text-center text-sm text-gray-600 dark:text-gray-400 tabular-nums select-none">{{ zoomPercent }}%</span>
          <button @click="zoomIn" :disabled="loadingPage" :title="t('app.zoom_in')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors disabled:opacity-50">
            <MagnifyingGlassPlusIcon class="h-5 w-5" />
          </button>
          <button
            @click="toggleViewMode"
            :disabled="loadingPage"
            :title="isDoublePage ? t('djvu.singlePage') : t('djvu.twoPages')"
            class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <DocumentIcon v-if="isDoublePage" class="h-5 w-5" />
            <BookOpenIcon v-else class="h-5 w-5" />
          </button>
          <button
            @click="toggleOddSide"
            :disabled="!isDoublePage || loadingPage"
            :title="oddOnRight ? t('djvu.oddPagesRight') : t('djvu.oddPagesLeft')"
            class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <ArrowLongRightIcon v-if="oddOnRight" class="h-5 w-5" />
            <ArrowLongLeftIcon v-else class="h-5 w-5" />
          </button>
          <button
            @click="toggleImmersive"
            :title="t('app.immersive_enter')"
            class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors"
          >
            <ArrowsPointingOutIcon class="h-5 w-5" />
          </button>
        </div>
      </div>
      
      <!-- TOC sidebar + viewer area -->
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
            <DjvuTocNode
              v-for="(entry, i) in toc"
              :key="i"
              :entry="entry"
              :level="0"
              @navigate="onTocNavigate"
            />
          </nav>
        </aside>

        <div ref="container" class="relative flex-grow min-w-0 overflow-auto bg-gray-200 dark:bg-gray-900 p-2 lg:p-4">
          <div v-if="loadingPage" class="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-black/50 z-10">
            <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
          </div>

          <!-- w-fit + mx-auto: centers when the spread fits, clamps the left
               margin to 0 when it overflows so both pages stay reachable by
               horizontal scroll. -->
          <div v-if="imageUrl || imageUrl2" class="flex flex-row items-start gap-1 md:gap-2 w-fit mx-auto">
            <img
              v-if="imageUrl"
              :src="imageUrl"
              :style="dispL ? { width: dispL.w + 'px', height: dispL.h + 'px' } : undefined"
              class="shrink-0 object-contain shadow-md bg-white"
              alt="DjVu Page"
            />
            <!-- Odd-right cover: blank left half so the cover page sits on the right. -->
            <div
              v-else-if="isDoublePage && imageUrl2 && dispR"
              :style="{ width: dispR.w + 'px', height: dispR.h + 'px' }"
              class="shrink-0"
            ></div>
            <img
              v-if="isDoublePage && imageUrl2"
              :src="imageUrl2"
              :style="dispR ? { width: dispR.w + 'px', height: dispR.h + 'px' } : undefined"
              class="shrink-0 object-contain shadow-md bg-white"
              alt="DjVu Page 2"
            />
          </div>
        </div>
      </div>

      <!-- Immersive floating controls. Sit outside the scrolling viewer area
           so they stay pinned to the viewport when a zoom mode (fit-width, or
           a custom scale) makes the page overflow vertically. -->
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
            <DjvuTocNode
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
    </template>
  </div>
</template>

<style scoped>
.djvu-resizable {
  resize: both;
  overflow: hidden;
  height: 80vh;
  min-height: 400px;
}
/* hide number input arrows */
input[type=number]::-webkit-inner-spin-button,
input[type=number]::-webkit-outer-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
input[type=number] {
  -moz-appearance: textfield;
}
</style>
