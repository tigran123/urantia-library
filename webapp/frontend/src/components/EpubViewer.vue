<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import ePub, { Book, Rendition, type Location } from 'epubjs'
import api from '../api'

const props = defineProps<{ path: string }>()

const viewer = ref<HTMLElement | null>(null)
let book: Book | null = null
let rendition: Rendition | null = null

const loading = ref(true)
const error = ref('')

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
      <div class="flex items-center space-x-2 w-full justify-between">
        <button @click="prevPage" class="px-4 py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors font-medium">Previous</button>
        <button @click="nextPage" class="px-4 py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors font-medium">Next</button>
      </div>
    </div>

    <div class="relative w-full flex-grow overflow-hidden bg-white dark:bg-gray-900 rounded-b-lg h-[70vh] min-h-[400px]">
      <div ref="viewer" class="absolute inset-0 p-4"></div>
    </div>
  </div>
</template>

