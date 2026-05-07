<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import ePub, { Book, Rendition, type Location } from 'epubjs'
import api from '../api'
import { useI18n } from 'vue-i18n'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{ path: string }>()

const viewer = ref<HTMLElement | null>(null)
let book: Book | null = null
let rendition: Rendition | null = null

const loading = ref(true)
const error = ref('')

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

// Wrap mutations so the reader stays on the same page after epub.js
// re-paginates with the new typography. Capturing the CFI lets us redisplay
// at the exact same content position regardless of how pages reflow.
const adjustEpubFont = (mutate: () => void) => {
  if (!rendition) { mutate(); return }
  // epub.js's `currentLocation()` actually returns the same `Location` shape
  // emitted by `relocated`, but its types say `DisplayedLocation`. Cast
  // through `unknown` to bypass the (incorrect) overlap check.
  const loc = rendition.currentLocation() as unknown as Location | undefined
  const cfi = loc?.start?.cfi
  mutate()
  applyTypography()
  if (!cfi) return
  // themes.fontSize/font injects CSS into the iframe but reflow happens
  // asynchronously. Wait for the next 'relocated' (which fires after the
  // continuous manager re-paginates), then redisplay at the saved CFI to
  // restore position. Fallback timeout covers cases where the layout didn't
  // visibly change and no relocate fires.
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

const incFont = () => adjustEpubFont(() => { fontScale.value = Math.min(2, fontScale.value + 0.1) })
const decFont = () => adjustEpubFont(() => { fontScale.value = Math.max(0.6, fontScale.value - 0.1) })
const resetFont = () => adjustEpubFont(() => { fontScale.value = 1 })
const onFontFamilyChange = (e: Event) => {
  const v = (e.target as HTMLSelectElement).value
  adjustEpubFont(() => { fontFamilyId.value = v })
}

const saveProgress = async (cfi: string) => {
  if (!cfi) return
  try {
    await api.post('/progress', {
      item_path: props.path,
      location: cfi
    })
  } catch (e) {
    console.error('Failed to save progress', e)
  }
}

const loadProgress = async () => {
  try {
    const res = await api.get(`/progress/${encodeURIComponent(props.path)}`)
    return res.data.location
  } catch (e: any) {
    if (e.response?.status !== 404) {
      console.error('Failed to load progress', e)
    }
    return null
  }
}

let saveTimeout: any

const applyTheme = () => {
  if (!rendition) return
  const isDark = document.documentElement.classList.contains('dark')
  if (isDark) {
    rendition.themes.register('dark', {
      body: { background: '#111827', color: '#f3f4f6' },
      a: { color: '#60a5fa' }
    })
    rendition.themes.select('dark')
  } else {
    rendition.themes.register('light', {
      body: { background: '#ffffff', color: '#111827' },
      a: { color: '#2563eb' }
    })
    rendition.themes.select('light')
  }
}

const initEpub = async () => {
  if (!viewer.value || !props.path) return
  
  loading.value = true
  error.value = ''
  
  try {
    const res = await api.get(`/files/${props.path.split('/').map(encodeURIComponent).join('/')}`, {
      responseType: 'arraybuffer'
    })
    
    book = ePub(res.data as ArrayBuffer)
    
    rendition = book.renderTo(viewer.value, {
      width: '100%',
      height: '100%',
      spread: 'none',
      manager: 'continuous',
      flow: 'paginated'
    })
    
    applyTheme()
    applyTypography()

    await book.ready

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
    
  } catch (e: any) {
    error.value = e.message || 'Failed to load EPUB'
  } finally {
    loading.value = false
  }
}

const prevPage = () => {
  if (rendition) rendition.prev()
}

const nextPage = () => {
  if (rendition) rendition.next()
}

onMounted(() => {
  initEpub()
  // Re-apply theme if user toggles dark mode while reading
  const observer = new MutationObserver(() => applyTheme())
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})

watch(() => props.path, () => {
  if (book) {
    book.destroy()
    book = null
    rendition = null
  }
  initEpub()
})

onBeforeUnmount(() => {
  clearTimeout(saveTimeout)
  if (book) {
    book.destroy()
    book = null
    rendition = null
  }
})
</script>

<template>
  <div class="epub-viewer relative flex flex-col items-center bg-gray-100 dark:bg-gray-800 rounded-lg shadow w-full h-[80vh]">
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-black/50 z-10 rounded-lg">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
    </div>
    <div v-else-if="error" class="absolute inset-0 flex items-center justify-center bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 rounded-lg z-10">
      {{ error }}
    </div>

    <!-- Toolbar -->
    <div class="w-full flex flex-wrap items-center justify-between p-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg shadow-sm gap-2 z-20">
      <div class="flex items-center gap-2">
        <button @click="prevPage" class="px-4 py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors font-medium">Previous</button>
        <button @click="nextPage" class="px-4 py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors font-medium">Next</button>
      </div>
      <div class="flex items-center gap-2 shrink-0">
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
      </div>
    </div>

    <div class="relative w-full flex-grow overflow-hidden bg-white dark:bg-gray-900 rounded-b-lg h-[70vh] min-h-[400px]">
      <div ref="viewer" class="absolute inset-0 p-4"></div>
    </div>
  </div>
</template>

