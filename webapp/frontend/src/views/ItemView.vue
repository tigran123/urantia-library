<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import { DocumentIcon, ArrowDownTrayIcon } from '@heroicons/vue/24/outline'
import { useI18n } from 'vue-i18n'
import DjvuViewer from '../components/DjvuViewer.vue'

const { t } = useI18n({ useScope: 'global' })
const route = useRoute()
const item = ref<any>(null)
const loading = ref(true)
const error = ref('')
const currentPath = ref('')
const originalTitle = ref(document.title)

const loadItem = async (path: string) => {
  loading.value = true
  error.value = ''
  try {
    const p = Array.isArray(path) ? path.join('/') : path || ''
    currentPath.value = p
    
    // We need to fetch the file details. We can use the /browse API on the parent directory
    // and find the specific file.
    const parts = p.split('/')
    const fileName = parts.pop()
    const parentPath = parts.join('/')
    
    const res = await api.get('/browse', { params: { path: parentPath } })
    const foundItem = res.data.items.find((i: any) => i.name === fileName)
    
    if (foundItem) {
      item.value = foundItem
      document.title = foundItem.name.replace(/\.[^/.]+$/, "")
    } else {
      error.value = 'Item not found'
    }
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  originalTitle.value = document.title
  loadItem(route.params.path as string)
})

onUnmounted(() => {
  document.title = originalTitle.value
})

watch(() => route.params.path, (newPath) => {
  loadItem(newPath as string)
})

const getFullUrl = (url: string) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}

const getDownloadUrl = () => {
  if (!item.value) return ''
  return getFullUrl(`/api/files/${item.value.path.split('/').map(encodeURIComponent).join('/')}`)
}

const formatBytes = (bytes: number, decimals = 2) => {
    if (!+bytes) return '0 Bytes'
    const k = 1024
    const dm = decimals < 0 ? 0 : decimals
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const formatDate = (dateString: string) => {
  if (!dateString) return t('app.unknown')
  return new Date(dateString).toLocaleString()
}

const fileExtension = computed(() => {
  if (!item.value || !item.value.name) return ''
  const parts = item.value.name.split('.')
  return parts.length > 1 ? parts.pop().toLowerCase() : ''
})

const isAudio = computed(() => ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac'].includes(fileExtension.value))
const isVideo = computed(() => ['mp4', 'webm', 'mkv', 'avi', 'mov'].includes(fileExtension.value))
const isImage = computed(() => ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'].includes(fileExtension.value))
const isPdf = computed(() => fileExtension.value === 'pdf')
const isDjvu = computed(() => fileExtension.value === 'djvu')
</script>

<template>
  <div class="w-full p-4 md:p-6">
    <div v-if="loading" class="flex justify-center py-12">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
    </div>
    
    <div v-else-if="error" class="bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 p-4 rounded-lg text-center">
      {{ error }}
    </div>
    
    <div v-else-if="item" class="space-y-8 transition-colors duration-300 bg-gray-50 dark:bg-gray-900 min-h-screen rounded-xl pb-12 pt-8">
      
      <!-- Main Content Grid -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-8 px-4 md:px-8">
        
        <!-- Left Column: Cover Image -->
        <div class="md:col-span-1 flex flex-col items-center">
          <div class="w-full max-w-sm aspect-[3/4] rounded-lg shadow-xl overflow-hidden bg-white dark:bg-gray-800 flex items-center justify-center border border-gray-200 dark:border-gray-700">
            <img v-if="item.cover_url" :src="getFullUrl(item.cover_url)" :alt="item.name" class="w-full h-full object-cover" />
            <DocumentIcon v-else class="w-32 h-32 text-gray-300 dark:text-gray-600" />
          </div>
        </div>
        
        <!-- Right Column: Metadata & Actions -->
        <div class="md:col-span-2 flex flex-col justify-center space-y-6">
          <div>
            <h1 class="text-2xl md:text-4xl font-serif font-bold text-gray-900 dark:text-gray-100 break-words leading-tight">
              {{ item.name.replace(/\.[^/.]+$/, "") }}
            </h1>
            <p class="mt-2 text-lg text-gray-500 dark:text-gray-400 font-sans break-all">
              {{ item.name }}
            </p>
          </div>
          
          <div v-if="item.description" class="prose dark:prose-invert max-w-none text-gray-700 dark:text-gray-300" v-html="item.description">
          </div>
          
          <!-- Metadata Table -->
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm">
            <table class="w-full text-sm text-left">
              <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.format') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400 uppercase font-semibold">{{ fileExtension }}</td>
                </tr>
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.size') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400">{{ formatBytes(item.size, 0) }} ({{ item.size }} bytes)</td>
                </tr>
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.modified') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400">{{ formatDate(item.mtime) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Actions -->
          <div class="flex flex-wrap gap-4 pt-4">
            <a :href="getDownloadUrl()" download class="inline-flex items-center px-6 py-3 border border-transparent text-base font-medium rounded-lg shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-colors">
              <ArrowDownTrayIcon class="-ml-1 mr-2 h-5 w-5" aria-hidden="true" />
              {{ t('app.download') }} {{ formatBytes(item.size, 0) }}
            </a>
          </div>
        </div>
      </div>

      <!-- Built-in Viewer -->
      <div class="px-4 md:px-8 pt-8 w-full">
        <h3 class="text-xl font-serif font-semibold mb-4 text-gray-800 dark:text-gray-200">{{ t('app.preview') }}</h3>
        
        <div class="rounded-xl overflow-hidden shadow-inner border border-gray-200 dark:border-gray-700 min-h-[500px] flex items-center justify-center bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
          
          <!-- Audio Player -->
          <audio v-if="isAudio" controls class="w-full max-w-md" :src="getDownloadUrl()">
            Your browser does not support the audio element.
          </audio>
          
          <!-- Video Player -->
          <video v-else-if="isVideo" controls class="w-full max-w-4xl" :src="getDownloadUrl()">
            Your browser does not support the video tag.
          </video>
          
          <!-- Image Viewer -->
          <img v-else-if="isImage" :src="getDownloadUrl()" class="max-w-full max-h-[80vh] object-contain" />
          
          <!-- PDF Viewer (iframe fallback) -->
          <iframe v-else-if="isPdf" :src="getDownloadUrl()" class="w-full h-[80vh] bg-white"></iframe>

          <!-- DjVu Viewer -->
          <DjvuViewer v-else-if="isDjvu" :path="item.path" />

          <!-- Unsupported -->
          <div v-else class="text-center p-8">
            <DocumentIcon class="mx-auto h-16 w-16 text-gray-400 mb-4" />
            <p class="text-lg">{{ t('app.preview_not_available') }}</p>
            <p class="text-sm mt-2 text-gray-500">{{ t('app.please_download') }}</p>
          </div>
          
        </div>
      </div>

    </div>
  </div>
</template>mplate>