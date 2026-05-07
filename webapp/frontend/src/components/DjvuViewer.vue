<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import api from '../api'
import { useI18n } from 'vue-i18n'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{
  path: string
}>()

const totalPages = ref(0)
const currentPage = ref(1)
const loadingMetadata = ref(true)
const loadingPage = ref(false)
const error = ref('')

const imageUrl = ref('')
const imageUrl2 = ref('')
const isDoublePage = ref(false)

const saveProgress = async (page: number) => {
  try {
    await api.post('/progress', {
      item_path: props.path,
      location: JSON.stringify({ page: page, isDoublePage: isDoublePage.value })
    })
  } catch (e) {
    console.error('Failed to save progress', e)
  }
}

const loadProgress = async () => {
  try {
    const res = await api.get(`/progress/${encodeURIComponent(props.path)}`)
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
    const res = await api.get('/djvu-metadata', { params: { path: props.path } })
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
  const res = await api.get('/djvu-page', { 
    params: { path: props.path, page },
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

const nextPage = () => {
  const step = isDoublePage.value ? 2 : 1
  if (currentPage.value < totalPages.value) {
    let next = currentPage.value + step
    if (next > totalPages.value) next = totalPages.value
    fetchPage(next)
  }
}

const prevPage = () => {
  const step = isDoublePage.value ? 2 : 1
  if (currentPage.value > 1) {
    let prev = currentPage.value - step
    if (prev < 1) prev = 1
    fetchPage(prev)
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
  if (props.path) {
    fetchMetadata()
  }
})

watch(() => props.path, (newPath) => {
  if (newPath) {
    fetchMetadata()
  }
})
</script>

<template>
  <div class="djvu-viewer flex flex-col items-center bg-gray-100 dark:bg-gray-800 rounded-lg shadow w-full">
    <div v-if="loadingMetadata" class="p-8">
      <div class="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-500 mx-auto"></div>
      <p class="mt-4 text-gray-600 dark:text-gray-400">{{ t('djvu.loadingDetails') }}</p>
    </div>
    
    <div v-else-if="error" class="p-8 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/30 rounded w-full text-center">
      {{ error }}
    </div>
    
    <template v-else-if="totalPages > 0">
      <!-- Toolbar -->
      <div class="w-full flex flex-wrap items-center justify-between p-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg shadow-sm gap-2">
        <div class="flex items-center space-x-2">
          <button 
            @click="prevPage" 
            :disabled="currentPage === 1 || loadingPage"
            class="px-3 py-1.5 md:px-4 md:py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium text-sm md:text-base"
          >
            {{ t('djvu.previous') }}
          </button>
          <button 
            @click="nextPage" 
            :disabled="(isDoublePage ? currentPage >= totalPages - 1 && totalPages > 1 : currentPage >= totalPages) || loadingPage"
            class="px-3 py-1.5 md:px-4 md:py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium text-sm md:text-base"
          >
            {{ t('djvu.next') }}
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
        
        <div>
          <button 
            @click="toggleViewMode" 
            :disabled="loadingPage"
            class="px-3 py-1.5 md:px-4 md:py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium text-sm md:text-base"
          >
            {{ isDoublePage ? t('djvu.singlePage') : t('djvu.twoPages') }}
          </button>
        </div>
      </div>
      
      <!-- Viewer area -->
      <div class="relative w-full flex-grow overflow-auto flex items-center justify-center bg-gray-200 dark:bg-gray-900 p-2 lg:p-4">
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
    </template>
  </div>
</template>

<style scoped>
.djvu-viewer {
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
