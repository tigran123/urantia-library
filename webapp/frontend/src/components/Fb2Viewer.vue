<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import api from '../api'
import { useI18n } from 'vue-i18n'
import { Bars3Icon, ChevronDoubleLeftIcon, ArrowsPointingOutIcon, ArrowsPointingInIcon } from '@heroicons/vue/24/outline'
import Fb2TocNode from './Fb2TocNode.vue'
import { viewerUrls, viewerParams, sourceHashId, type ViewerSource } from './viewerSource'

interface TocEntry {
  title: string
  anchor: number | null
  children: TocEntry[]
}

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{ source: ViewerSource }>()
const hashId = computed(() => sourceHashId(props.source))

const loading = ref(true)
const error = ref('')
const html = ref('')
const title = ref('')
const authors = ref<string[]>([])
const notes = ref<Record<string, string>>({})
const toc = ref<TocEntry[]>([])
const tocOpen = ref(true)
const immersive = ref(false)

const toggleImmersive = () => { immersive.value = !immersive.value }

watch(immersive, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
  document.documentElement.style.overflow = v ? 'hidden' : ''
  if (v) tocOpen.value = false
})

const onKeyDown = (e: KeyboardEvent) => {
  if (e.key === 'Escape' && immersive.value) {
    e.preventDefault()
    immersive.value = false
  }
}

const FONT_SCALE_KEY = 'reader-font-scale'
const loadFontScale = (): number => {
  const raw = localStorage.getItem(FONT_SCALE_KEY)
  const n = raw ? parseFloat(raw) : NaN
  return Number.isFinite(n) && n >= 0.6 && n <= 2 ? n : 1
}
const fontScale = ref(loadFontScale())
watch(fontScale, (v) => {
  try { localStorage.setItem(FONT_SCALE_KEY, String(v)) } catch {}
})

const FONT_FAMILY_KEY = 'reader-font-family'
const FONT_OPTIONS = [
  { id: 'sans',  label: 'Sans',  stack: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' },
  { id: 'serif', label: 'Serif', stack: 'Georgia, "Times New Roman", "Liberation Serif", serif' },
  { id: 'mono',  label: 'Mono',  stack: '"SF Mono", Menlo, Consolas, "Liberation Mono", monospace' },
]
const loadFontFamilyId = (): string => {
  const raw = localStorage.getItem(FONT_FAMILY_KEY) || ''
  return FONT_OPTIONS.find(o => o.id === raw)?.id || 'sans'
}
const fontFamilyId = ref(loadFontFamilyId())
watch(fontFamilyId, (v) => {
  try { localStorage.setItem(FONT_FAMILY_KEY, v) } catch {}
})
const fontFamily = computed(
  () => FONT_OPTIONS.find(o => o.id === fontFamilyId.value)?.stack || FONT_OPTIONS[0].stack
)

const scrollEl = ref<HTMLElement | null>(null)

const tooltip = ref<{ show: boolean; x: number; y: number; html: string }>({
  show: false, x: 0, y: 0, html: ''
})

let saveTimeout: ReturnType<typeof setTimeout> | null = null
let hideTooltipTimer: ReturnType<typeof setTimeout> | null = null
let restoring = false
let lastSavedAnchor = -1

const saveProgress = async (anchor: number) => {
  if (anchor === lastSavedAnchor) return
  if (!hashId.value) return
  lastSavedAnchor = anchor
  try {
    await api.post('/progress', {
      hash_id: hashId.value,
      location: JSON.stringify({ anchor })
    })
  } catch (e) {
    console.error('Failed to save FB2 progress', e)
  }
}

const loadProgress = async (): Promise<number | null> => {
  if (!hashId.value) return null
  try {
    const res = await api.get(`/progress/${encodeURIComponent(hashId.value)}`)
    try {
      const data = JSON.parse(res.data.location)
      const a = parseInt(data.anchor)
      return Number.isFinite(a) ? a : null
    } catch {
      return null
    }
  } catch (e: any) {
    if (e.response?.status !== 404) console.error('Failed to load FB2 progress', e)
    return null
  }
}

const findTopAnchor = (): number | null => {
  const container = scrollEl.value
  if (!container) return null
  const containerTop = container.getBoundingClientRect().top
  const anchored = container.querySelectorAll<HTMLElement>('[data-anchor]')
  let best: number | null = null
  let bestDelta = Infinity
  for (const el of anchored) {
    const top = el.getBoundingClientRect().top - containerTop
    // Topmost element whose top is at or just above the viewport top edge
    if (top <= 4) {
      const delta = Math.abs(top)
      if (delta < bestDelta) {
        bestDelta = delta
        best = parseInt(el.dataset.anchor || '')
      }
    } else {
      break
    }
  }
  return best
}

const onScroll = () => {
  // Tooltip is positioned in viewport coords; dismiss it when content scrolls
  // out from under it rather than letting it drift.
  if (tooltip.value.show) tooltip.value.show = false
  if (restoring) return
  if (saveTimeout) clearTimeout(saveTimeout)
  saveTimeout = setTimeout(() => {
    const a = findTopAnchor()
    if (a !== null) saveProgress(a)
  }, 600)
}

const scrollToAnchor = (anchor: number) => {
  const container = scrollEl.value
  if (!container) return
  const el = container.querySelector<HTMLElement>(`#fb2-a-${anchor}`)
  if (!el) return
  const containerTop = container.getBoundingClientRect().top
  const elTop = el.getBoundingClientRect().top
  container.scrollTop += elTop - containerTop
}

const adjustFont = async (mutate: () => void) => {
  // Capture the reading position *before* mutating, then restore it after the
  // reflow. Without this, scrollTop stays put while content height changes,
  // so the user lands on different text after a font-size change.
  const anchor = findTopAnchor()
  mutate()
  await nextTick()
  if (anchor !== null) {
    restoring = true
    scrollToAnchor(anchor)
    setTimeout(() => { restoring = false }, 250)
  }
}

const incFont = () => adjustFont(() => { fontScale.value = Math.min(2, fontScale.value + 0.1) })
const decFont = () => adjustFont(() => { fontScale.value = Math.max(0.6, fontScale.value - 0.1) })
const resetFont = () => adjustFont(() => { fontScale.value = 1 })

const onFontFamilyChange = (e: Event) => {
  const v = (e.target as HTMLSelectElement).value
  adjustFont(() => { fontFamilyId.value = v })
}

const onTocNavigate = (anchor: number) => {
  scrollToAnchor(anchor)
  if (immersive.value) tocOpen.value = false
}

const TOOLTIP_W = 380
const TOOLTIP_MARGIN = 8

const showTooltipFor = (link: HTMLElement, noteHtml: string) => {
  if (hideTooltipTimer) { clearTimeout(hideTooltipTimer); hideTooltipTimer = null }
  const linkRect = link.getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight
  let x = linkRect.left
  if (x + TOOLTIP_W + TOOLTIP_MARGIN > vw) x = vw - TOOLTIP_W - TOOLTIP_MARGIN
  if (x < TOOLTIP_MARGIN) x = TOOLTIP_MARGIN
  // Place below by default; flip above if it would overflow the viewport
  let y = linkRect.bottom + 6
  if (y + 200 > vh && linkRect.top > 200) y = linkRect.top - 6 - 200
  tooltip.value = { show: true, x, y, html: noteHtml }
}

const scheduleHideTooltip = () => {
  if (hideTooltipTimer) clearTimeout(hideTooltipTimer)
  hideTooltipTimer = setTimeout(() => {
    tooltip.value.show = false
  }, 150)
}

const cancelHideTooltip = () => {
  if (hideTooltipTimer) { clearTimeout(hideTooltipTimer); hideTooltipTimer = null }
}

const onContentClick = (e: MouseEvent) => {
  // Block all internal-anchor clicks inside the FB2 content. The app's router
  // treats `#fragment` URL changes as navigation, which blanks the page.
  const target = (e.target as HTMLElement | null)?.closest?.('a[href^="#"]') as HTMLAnchorElement | null
  if (!target) return
  e.preventDefault()
  const noteId = (target.getAttribute('href') || '').slice(1)
  const noteHtml = notes.value[noteId]
  if (noteHtml) showTooltipFor(target, noteHtml)
}

// Pointer events (gated on pointerType === 'mouse') instead of mouseover/out:
// on touch, Android Chrome synthesizes mouseover at touchstart, which was
// disturbing the selection lifecycle and suppressing the Copy floating
// toolbar. Touch users get the footnote via tap (onContentClick); only mice
// trigger the hover tooltip now.
const onContentPointerEnter = (e: PointerEvent) => {
  if (e.pointerType !== 'mouse') return
  const target = (e.target as HTMLElement | null)?.closest?.('a.fb2-note') as HTMLAnchorElement | null
  if (!target) return
  const noteId = (target.getAttribute('href') || '').slice(1)
  const noteHtml = notes.value[noteId]
  if (noteHtml) showTooltipFor(target, noteHtml)
}

const onContentPointerLeave = (e: PointerEvent) => {
  if (e.pointerType !== 'mouse') return
  const target = (e.target as HTMLElement | null)?.closest?.('a.fb2-note')
  if (!target) return
  const related = e.relatedTarget as HTMLElement | null
  if (related?.closest?.('.fb2-tooltip')) return
  scheduleHideTooltip()
}

const initFb2 = async () => {
  loading.value = true
  error.value = ''
  html.value = ''
  notes.value = {}
  tooltip.value.show = false
  lastSavedAnchor = -1
  try {
    const urls = viewerUrls(props.source)
    const res = await api.get(urls.fb2Content, { params: viewerParams(props.source) })
    title.value = res.data.title || ''
    authors.value = res.data.authors || []
    html.value = res.data.html || ''
    notes.value = res.data.notes || {}
    toc.value = res.data.toc || []
    const saved = await loadProgress()
    await nextTick()
    if (saved !== null) {
      restoring = true
      scrollToAnchor(saved)
      lastSavedAnchor = saved
      // Release the restore guard after the scroll settles so we don't re-save
      // the same anchor we just restored to.
      setTimeout(() => { restoring = false }, 250)
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message || 'Failed to load FB2'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
  initFb2()
})

watch(() => props.source, () => {
  initFb2()
}, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  document.body.style.overflow = ''
  document.documentElement.style.overflow = ''
  if (saveTimeout) {
    clearTimeout(saveTimeout)
    const a = findTopAnchor()
    if (a !== null) saveProgress(a)
  }
  if (hideTooltipTimer) clearTimeout(hideTooltipTimer)
})
</script>

<template>
  <div
    :class="[
      'fb2-viewer flex flex-col items-stretch bg-gray-100 dark:bg-gray-800 w-full',
      immersive
        ? 'fixed inset-0 z-50 h-dvh rounded-none'
        : 'relative rounded-lg shadow h-[80vh]'
    ]"
  >
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-black/50 z-10 rounded-lg">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
    </div>
    <div v-else-if="error" class="absolute inset-0 flex items-center justify-center bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded-lg z-10 p-4 text-center">
      {{ error }}
    </div>

    <!-- Toolbar (hidden in immersive) -->
    <div v-if="!immersive" class="w-full flex flex-wrap items-center justify-between p-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg shadow-sm gap-2 z-20">
      <div class="text-sm text-gray-700 dark:text-gray-300 truncate min-w-0">
        <span v-if="title" class="font-semibold">{{ title }}</span>
        <span v-if="authors.length" class="ml-2 text-gray-500 dark:text-gray-400">— {{ authors.join(', ') }}</span>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-2">
        <select
          :value="fontFamilyId"
          @change="onFontFamilyChange"
          :title="t('app.font_family')"
          class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded text-sm cursor-pointer border-0 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option v-for="o in FONT_OPTIONS" :key="o.id" :value="o.id" :style="{ fontFamily: o.stack }">{{ o.label }}</option>
        </select>
        <button @click="decFont" :title="t('app.font_smaller')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded">A−</button>
        <button @click="resetFont" :title="t('app.font_reset')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded">A</button>
        <button @click="incFont" :title="t('app.font_larger')" class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded">A+</button>
        <button
          @click="toggleImmersive"
          :title="t('app.immersive_enter')"
          class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded"
        >
          <ArrowsPointingOutIcon class="h-5 w-5" />
        </button>
      </div>
    </div>

    <!-- TOC sidebar + Scrollable text area -->
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
          <Fb2TocNode
            v-for="(entry, i) in toc"
            :key="i"
            :entry="entry"
            :level="0"
            @navigate="onTocNavigate"
          />
        </nav>
      </aside>

      <div
        ref="scrollEl"
        @scroll.passive="onScroll"
        class="fb2-scroll flex-grow min-w-0 overflow-y-auto bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
      >
        <div
          class="fb2-content w-full px-6 py-8 leading-relaxed"
          :style="{ fontSize: `${fontScale}rem`, fontFamily }"
          v-html="html"
          @click="onContentClick"
          @pointerover="onContentPointerEnter"
          @pointerout="onContentPointerLeave"
        ></div>
      </div>
    </div>

    <!-- Floating controls in Reading Mode -->
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
          <Fb2TocNode
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
    </template>

    <Teleport to="body">
      <div
        v-if="tooltip.show"
        class="fb2-tooltip fixed z-50 max-h-80 overflow-auto rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 shadow-xl px-3 py-2 text-sm leading-snug"
        :style="{ left: `${tooltip.x}px`, top: `${tooltip.y}px`, width: `${TOOLTIP_W}px` }"
        @mouseenter="cancelHideTooltip"
        @mouseleave="scheduleHideTooltip"
        v-html="tooltip.html"
      ></div>
    </Teleport>
  </div>
</template>

<style>
/* Explicit selection enablement. Browsers normally infer this from text
   content, but on Android Chrome the floating Copy toolbar wouldn't appear
   over selected FB2 text without these declarations; EPUB was unaffected
   because epub.js renders into an iframe with its own selection context. */
.fb2-content {
  -webkit-user-select: text;
  user-select: text;
  cursor: text;
  -webkit-touch-callout: default;
}
.fb2-content a {
  cursor: pointer;
}

.fb2-content .fb2-section-title { font-weight: 600; margin: 1.5em 0 0.75em; }
.fb2-content h2.fb2-section-title { font-size: 1.5em; }
.fb2-content h3.fb2-section-title { font-size: 1.3em; }
.fb2-content h4.fb2-section-title,
.fb2-content h5.fb2-section-title,
.fb2-content h6.fb2-section-title { font-size: 1.1em; }
.fb2-content .fb2-body-title { font-size: 1.75em; font-weight: 700; margin: 0 0 1em; text-align: center; }
.fb2-content .fb2-subtitle { font-size: 1.1em; font-weight: 600; margin: 1em 0 0.5em; }
.fb2-content .fb2-p { margin: 0 0 0.75em; text-indent: 1.5em; text-align: justify; }
.fb2-content .fb2-section > .fb2-p:first-of-type { text-indent: 0; }
.fb2-content .fb2-empty-line { height: 1em; }
.fb2-content .fb2-image-wrap { margin: 1em 0; text-align: center; }
.fb2-content .fb2-image { max-width: 100%; height: auto; }
.fb2-content .fb2-inline-img { max-height: 1.2em; vertical-align: middle; }
.fb2-content .fb2-epigraph,
.fb2-content .fb2-cite { margin: 1em 2em; font-style: italic; border-left: 3px solid rgba(127,127,127,0.3); padding-left: 1em; }
.fb2-content .fb2-text-author { margin-top: 0.5em; font-style: italic; text-align: right; }
.fb2-content .fb2-poem { margin: 1em 0; }
.fb2-content .fb2-poem-title { font-weight: 600; margin-bottom: 0.5em; }
.fb2-content .fb2-stanza { margin: 0.5em 0; }
.fb2-content .fb2-v { margin-left: 2em; }
.fb2-content .fb2-link { color: #2563eb; text-decoration: underline; }
.dark .fb2-content .fb2-link { color: #60a5fa; }
.fb2-content .fb2-note {
  cursor: help;
  text-decoration: none;
  font-size: 0.75em;
  vertical-align: super;
  padding: 0 0.15em;
}
.fb2-tooltip p { margin: 0 0 0.5em; }
.fb2-tooltip p:last-child { margin-bottom: 0; }
</style>
