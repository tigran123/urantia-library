<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
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
import { viewerUrls, sourceHashId, type ViewerSource } from './viewerSource'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{ source: ViewerSource }>()
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
const tocOpen = ref(true)
const bookTitle = ref('')
const bookAuthors = ref<string[]>([])
const immersive = ref(false)

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

const applyTypography = () => {
  if (!rendition) return
  rendition.themes.fontSize(`${Math.round(fontScale.value * 100)}%`)
  rendition.themes.font(fontFamily.value)
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

const saveProgress = async (cfi: string) => {
  if (!cfi || !hashId.value) return
  try {
    await api.post('/progress', { hash_id: hashId.value, location: cfi })
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

const toggleImmersive = () => { immersive.value = !immersive.value }

const onKeyDown = (e: KeyboardEvent) => {
  if (!immersive.value) return
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
  if (e.key === 'Escape') { e.preventDefault(); immersive.value = false }
  else if (e.key === 'PageDown') { e.preventDefault(); nextPage() }
  else if (e.key === 'PageUp') { e.preventDefault(); prevPage() }
}

// When entering immersive: lock body scroll so accidental swipes near the
// page edges don't drift the underlying app. When leaving: restore.
// The ResizeObserver picks up the dimensional change and handles
// repagination, so no explicit resize+redisplay is needed here — calling
// resize() ourselves caused the rendition to drop the current CFI.
watch(immersive, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
  document.documentElement.style.overflow = v ? 'hidden' : ''
  if (v) tocOpen.value = false
})

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
    })

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

watch(() => props.source, () => {
  destroyBook()
  initEpub()
}, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  clearTimeout(saveTimeout)
  themeObs?.disconnect()
  themeObs = null
  document.body.style.overflow = ''
  document.documentElement.style.overflow = ''
  destroyBook()
})
</script>

<template>
  <div
    :class="[
      'epub-viewer flex flex-col items-stretch bg-gray-100 dark:bg-gray-800 w-full overscroll-contain',
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
      <div class="flex items-center gap-1 sm:gap-2 shrink-0">
        <select
          :value="fontFamilyId"
          @change="onFontFamilyChange"
          :title="t('app.font_family')"
          class="px-1 sm:px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm cursor-pointer border-0 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option v-for="o in FONT_OPTIONS" :key="o.id" :value="o.id" :style="{ fontFamily: o.stack }">{{ o.label }}</option>
        </select>
        <button @click="decFont" :title="t('app.font_smaller')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm">A−</button>
        <button @click="resetFont" :title="t('app.font_reset')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm">A</button>
        <button @click="incFont" :title="t('app.font_larger')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm">A+</button>
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
      </aside>

      <div class="relative flex-grow min-w-0 bg-white dark:bg-gray-900 overflow-hidden">
        <div ref="viewer" class="absolute inset-0" :class="immersive ? 'p-0' : 'p-1 sm:p-4'"></div>

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
            <nav class="flex-grow overflow-auto p-1 text-gray-800 dark:text-gray-200">
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
