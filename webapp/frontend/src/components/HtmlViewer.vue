<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import api from '../api'
import { useI18n } from 'vue-i18n'
import { Bars3Icon, ChevronDoubleLeftIcon, CodeBracketIcon, DocumentTextIcon, ArrowsPointingOutIcon, ArrowsPointingInIcon } from '@heroicons/vue/24/outline'
import MdTocNode from './MdTocNode.vue'
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
const raw = ref('')
const title = ref('')
const toc = ref<TocEntry[]>([])
const tocOpen = ref(true)
const rawMode = ref(false)
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
  const r = localStorage.getItem(FONT_SCALE_KEY)
  const n = r ? parseFloat(r) : NaN
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
  const r = localStorage.getItem(FONT_FAMILY_KEY) || ''
  return FONT_OPTIONS.find(o => o.id === r)?.id || 'serif'
}
const fontFamilyId = ref(loadFontFamilyId())
watch(fontFamilyId, (v) => {
  try { localStorage.setItem(FONT_FAMILY_KEY, v) } catch {}
})
const fontFamily = computed(
  () => FONT_OPTIONS.find(o => o.id === fontFamilyId.value)?.stack || FONT_OPTIONS[0].stack
)

const scrollEl = ref<HTMLElement | null>(null)

let saveTimeout: ReturnType<typeof setTimeout> | null = null
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
    console.error('Failed to save HTML progress', e)
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
    if (e.response?.status !== 404) console.error('Failed to load HTML progress', e)
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
  if (restoring) return
  if (rawMode.value) return
  if (saveTimeout) clearTimeout(saveTimeout)
  saveTimeout = setTimeout(() => {
    const a = findTopAnchor()
    if (a !== null) saveProgress(a)
  }, 600)
}

const toggleRaw = async () => {
  const container = scrollEl.value
  const ratio = container && container.scrollHeight > 0
    ? container.scrollTop / container.scrollHeight
    : 0
  rawMode.value = !rawMode.value
  await nextTick()
  if (!container) return
  restoring = true
  container.scrollTop = ratio * container.scrollHeight
  setTimeout(() => { restoring = false }, 250)
}

const scrollToAnchor = (anchor: number) => {
  const container = scrollEl.value
  if (!container) return
  const el = container.querySelector<HTMLElement>(`#md-a-${anchor}`)
  if (!el) return
  const containerTop = container.getBoundingClientRect().top
  const elTop = el.getBoundingClientRect().top
  container.scrollTop += elTop - containerTop
}

const adjustFont = async (mutate: () => void) => {
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

const onContentClick = (e: MouseEvent) => {
  // In-document fragment links would otherwise let the SPA hash router blank
  // the page (mirroring MdViewer's behavior).
  const target = (e.target as HTMLElement | null)?.closest?.('a[href^="#"]') as HTMLAnchorElement | null
  if (!target) return
  e.preventDefault()
}

const initHtml = async () => {
  loading.value = true
  error.value = ''
  html.value = ''
  raw.value = ''
  toc.value = []
  lastSavedAnchor = -1
  try {
    const urls = viewerUrls(props.source)
    const res = await api.get(urls.htmlContent, { params: viewerParams(props.source) })
    title.value = res.data.title || ''
    html.value = res.data.html || ''
    raw.value = res.data.raw || ''
    toc.value = res.data.toc || []
    const saved = await loadProgress()
    await nextTick()
    if (saved !== null) {
      restoring = true
      scrollToAnchor(saved)
      lastSavedAnchor = saved
      setTimeout(() => { restoring = false }, 250)
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message || 'Failed to load file'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
  initHtml()
})

watch(() => props.source, () => {
  initHtml()
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
})
</script>

<template>
  <div
    :class="[
      'html-viewer flex flex-col items-stretch bg-gray-100 dark:bg-gray-800 w-full',
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

    <div v-if="!immersive" class="w-full flex items-center justify-between p-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg shadow-sm gap-2 z-20">
      <div class="text-sm text-gray-700 dark:text-gray-300 truncate">
        <span v-if="title" class="font-semibold">{{ title }}</span>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button
          @click="toggleRaw"
          :title="rawMode ? t('app.view_rendered') : t('app.view_raw')"
          :aria-label="rawMode ? t('app.view_rendered') : t('app.view_raw')"
          class="p-1.5 text-gray-800 dark:text-gray-200 rounded"
          :class="rawMode
            ? 'bg-blue-100 hover:bg-blue-200 dark:bg-blue-900 dark:hover:bg-blue-800'
            : 'bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600'"
        >
          <DocumentTextIcon v-if="rawMode" class="w-5 h-5" />
          <CodeBracketIcon v-else class="w-5 h-5" />
        </button>
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
          <ArrowsPointingOutIcon class="w-5 h-5" />
        </button>
      </div>
    </div>

    <div class="flex flex-row flex-grow min-h-0 overflow-hidden" :class="immersive ? '' : 'rounded-b-lg'">
      <aside
        v-if="!rawMode && !immersive"
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
          <MdTocNode
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
        class="html-scroll flex-grow min-w-0 overflow-y-auto bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
      >
        <div
          v-if="!rawMode"
          class="html-content w-full max-w-4xl mx-auto px-6 py-8 leading-relaxed"
          :style="{ fontSize: `${fontScale}rem`, fontFamily }"
          v-html="html"
          @click="onContentClick"
        ></div>
        <pre
          v-else
          class="html-raw w-full max-w-4xl mx-auto px-6 py-8 whitespace-pre-wrap break-words"
          :style="{ fontSize: `${fontScale}rem`, fontFamily }"
        >{{ raw }}</pre>
      </div>
    </div>

    <template v-if="immersive">
      <!-- TOC overlay drawer -->
      <aside
        v-if="!rawMode && tocOpen"
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
          <MdTocNode
            v-for="(entry, i) in toc"
            :key="i"
            :entry="entry"
            :level="0"
            @navigate="onTocNavigate"
          />
        </nav>
      </aside>

      <button
        v-if="!rawMode && !tocOpen"
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
  </div>
</template>

<style>
.html-content h1 { font-size: 1.9em; font-weight: 700; margin: 0.4em 0 0.6em; }
.html-content h2 { font-size: 1.55em; font-weight: 700; margin: 1.2em 0 0.5em; }
.html-content h3 { font-size: 1.3em; font-weight: 600; margin: 1em 0 0.4em; }
.html-content h4, .html-content h5, .html-content h6 { font-size: 1.1em; font-weight: 600; margin: 1em 0 0.3em; }
.html-content p { margin: 0 0 0.85em; }
.html-content a { color: #2563eb; text-decoration: underline; }
.dark .html-content a { color: #60a5fa; }
.html-content img { max-width: 100%; height: auto; margin: 1em 0; }
.html-content blockquote {
  margin: 1em 0;
  padding: 0.25em 0 0.25em 1em;
  border-left: 4px solid rgba(127, 127, 127, 0.35);
  color: inherit;
  font-style: italic;
}
.html-content ul, .html-content ol { margin: 0 0 0.85em 1.5em; padding: 0; }
.html-content ul { list-style: disc; }
.html-content ol { list-style: decimal; }
.html-content li { margin: 0.15em 0; }
.html-content hr {
  border: 0;
  border-top: 1px solid rgba(127, 127, 127, 0.4);
  margin: 1.5em 0;
}
.html-content code {
  background: rgba(127, 127, 127, 0.15);
  padding: 0.1em 0.35em;
  border-radius: 3px;
  font-family: "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 0.92em;
}
.html-content pre {
  background: rgba(127, 127, 127, 0.12);
  padding: 0.75em 1em;
  border-radius: 6px;
  margin: 1em 0;
  overflow-x: auto;
  white-space: pre;
  font-family: "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 0.9em;
  line-height: 1.45;
}
.html-content pre code { background: transparent; padding: 0; border-radius: 0; font-size: inherit; }
.html-content table {
  border-collapse: collapse;
  margin: 1em 0;
  width: auto;
  max-width: 100%;
}
.html-content th, .html-content td {
  border: 1px solid rgba(127, 127, 127, 0.4);
  padding: 0.35em 0.6em;
  text-align: left;
  vertical-align: top;
}
.html-content th { background: rgba(127, 127, 127, 0.12); font-weight: 600; }

/* Compact rendering for the ItemView cover-slot preview. */
.html-content--preview h1 { font-size: 1.25em; margin: 0 0 0.3em; }
.html-content--preview h2 { font-size: 1.15em; margin: 0.4em 0 0.25em; }
.html-content--preview h3,
.html-content--preview h4,
.html-content--preview h5,
.html-content--preview h6 { font-size: 1.05em; margin: 0.3em 0 0.2em; }
.html-content--preview p { margin: 0 0 0.4em; }
.html-content--preview ul, .html-content--preview ol { margin: 0 0 0.4em 1.1em; }
.html-content--preview li { margin: 0; }
.html-content--preview img { display: none; }
.html-content--preview pre {
  padding: 0.25em 0.5em;
  margin: 0.35em 0;
  font-size: 0.9em;
  line-height: 1.3;
}
.html-content--preview blockquote { margin: 0.35em 0; padding-left: 0.5em; }
.html-content--preview hr { margin: 0.4em 0; }
.html-content--preview a { color: inherit; text-decoration: none; }
.html-content--preview table { font-size: 0.85em; }
.html-content--preview th, .html-content--preview td { padding: 0.15em 0.3em; }
</style>
