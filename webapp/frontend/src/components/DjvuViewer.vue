<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import api from '../api'
import { useI18n } from 'vue-i18n'
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  BookOpenIcon,
  DocumentIcon,
  ArrowsPointingOutIcon,
  ArrowsPointingInIcon,
} from '@heroicons/vue/24/outline'
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
const immersive = ref(false)
const container = ref<HTMLElement | null>(null)

const toggleImmersive = () => { immersive.value = !immersive.value }

// Lock body scroll while immersive so accidental swipes near the page edges
// don't drift the underlying app, and to ensure the floating controls don't
// sit behind the browser chrome.
watch(immersive, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
  document.documentElement.style.overflow = v ? 'hidden' : ''
})

// PgDn/PgUp scroll the viewport within the current page; only when already
// at the bottom/top does the keypress flip to the next/prev page. Today the
// image is fit-to-container so there is nothing to scroll and we always
// turn the page — once Fit Width is added, this handler will start
// scrolling first.
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

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  document.body.style.overflow = ''
  document.documentElement.style.overflow = ''
})

const saveProgress = async (page: number) => {
  if (!hashId.value) return
  try {
    await api.post('/progress', {
      hash_id: hashId.value,
      location: JSON.stringify({ page: page, isDoublePage: isDoublePage.value })
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
      return parseInt(data.page)
    } catch {
      // Fallback for old progress string format
      return parseInt(res.data.location)
    }
  } catch (e: any) {
    return null
  }
}

const fetchMetadata = async () => {
  loadingMetadata.value = true
  error.value = ''
  try {
    const urls = viewerUrls(props.source)
    const res = await api.get(urls.djvuMeta, { params: viewerParams(props.source) })
    totalPages.value = res.data.total_pages
    if (totalPages.value > 0) {
      const savedPage = await loadProgress()
      await fetchPage(savedPage && savedPage <= totalPages.value ? savedPage : 1)
    }
  } catch (err: any) {
    error.value = err.response?.data?.detail || 'Failed to load DjVu metadata'
  } finally {
    loadingMetadata.value = false
  }
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

const fetchPage = async (page: number) => {
  if (page < 1 || page > totalPages.value) return
  loadingPage.value = true
  error.value = ''
  
  try {
    const promises = [fetchPageData(page)]
    if (isDoublePage.value && page + 1 <= totalPages.value) {
      promises.push(fetchPageData(page + 1))
    }
    
    const results = await Promise.all(promises)
    
    if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
    if (imageUrl2.value) URL.revokeObjectURL(imageUrl2.value)
    
    imageUrl.value = results[0]
    imageUrl2.value = results.length > 1 ? results[1] : ''
    currentPage.value = page
    saveProgress(page)
  } catch (err: any) {
    error.value = err.message || 'Failed to load page'
  } finally {
    loadingPage.value = false
  }
}

// Turning the page resets the viewport: Next lands at the top of the new
// page, Prev at the bottom — so the reader's eye position carries over.
const nextPage = async () => {
  const step = isDoublePage.value ? 2 : 1
  if (currentPage.value < totalPages.value) {
    let next = currentPage.value + step
    if (next > totalPages.value) next = totalPages.value
    await fetchPage(next)
    await nextTick()
    if (container.value) container.value.scrollTop = 0
  }
}

const prevPage = async () => {
  const step = isDoublePage.value ? 2 : 1
  if (currentPage.value > 1) {
    let prev = currentPage.value - step
    if (prev < 1) prev = 1
    await fetchPage(prev)
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

onMounted(() => {
  fetchMetadata()
})

watch(() => props.source, () => {
  fetchMetadata()
}, { deep: true })
</script>

<template>
  <div
    :class="[
      'djvu-viewer flex flex-col items-center bg-gray-100 dark:bg-gray-800 w-full overscroll-contain',
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
            :disabled="currentPage === 1 || loadingPage"
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
        
        <div class="flex items-center space-x-2">
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
            @click="toggleImmersive"
            :title="t('app.immersive_enter')"
            class="px-2 py-1 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors"
          >
            <ArrowsPointingOutIcon class="h-5 w-5" />
          </button>
        </div>
      </div>
      
      <!-- Viewer area -->
      <div ref="container" class="relative w-full flex-grow overflow-auto flex items-center justify-center bg-gray-200 dark:bg-gray-900 p-2 lg:p-4">
        <div v-if="loadingPage" class="absolute inset-0 flex items-center justify-center bg-white/50 dark:bg-black/50 z-10">
          <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
        </div>
        
        <div v-if="imageUrl" class="flex flex-row items-center justify-center h-full w-full gap-1 md:gap-2">
          <img
            :src="imageUrl"
            :class="['max-h-full object-contain shadow-md bg-white', isDoublePage ? 'max-w-[calc(50%-0.125rem)] md:max-w-[calc(50%-0.25rem)]' : 'max-w-full']"
            alt="DjVu Page"
          />
          <img
            v-if="isDoublePage && imageUrl2"
            :src="imageUrl2"
            class="max-h-full max-w-[calc(50%-0.125rem)] md:max-w-[calc(50%-0.25rem)] object-contain shadow-md bg-white"
            alt="DjVu Page 2"
          />
        </div>
      </div>

      <!-- Immersive floating controls. Sit outside the scrolling viewer area
           so they stay pinned to the viewport when zoom modes (fit-width)
           later cause the page to overflow vertically. -->
      <template v-if="immersive">
        <button
          @click="toggleImmersive"
          :title="t('app.immersive_exit')"
          class="absolute top-2 right-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white"
        >
          <ArrowsPointingInIcon class="h-5 w-5" />
        </button>
        <button
          @click="prevPage"
          :disabled="currentPage === 1 || loadingPage"
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
