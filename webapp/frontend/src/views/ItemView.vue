<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted, defineAsyncComponent, inject, type Ref } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import { DocumentIcon, ArrowDownTrayIcon, BookmarkIcon, PencilSquareIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'
import { useI18n } from 'vue-i18n'
import DjvuViewer from '../components/DjvuViewer.vue'
import EpubViewer from '../components/EpubViewer.vue'
import ImageViewer from '../components/ImageViewer.vue'
import Fb2Viewer from '../components/Fb2Viewer.vue'
import MdViewer from '../components/MdViewer.vue'
import HtmlViewer from '../components/HtmlViewer.vue'
import BookMetadataEditor from '../components/BookMetadataEditor.vue'
// pdfjs-dist is ~1MB; keep it out of the main bundle by lazy-loading.
const PdfViewer = defineAsyncComponent(() => import('../components/PdfViewer.vue'))

const { t } = useI18n({ useScope: 'global' })
const route = useRoute()
const currentUser = inject<Ref<{ is_admin?: boolean } | null>>('currentUser', ref(null))
const item = ref<any>(null)
const loading = ref(true)
const error = ref('')
const currentPath = ref('')
const originalTitle = ref(document.title)
const favoriteIds = ref<Set<string>>(new Set())
const editingId = ref<string | null>(null)

const openEditor = () => {
  if (item.value?.hash_id) editingId.value = item.value.hash_id
}

const onEditorSaved = (updated: any) => {
  if (!item.value || item.value.hash_id !== updated.id) return
  item.value.title = updated.title
  item.value.author = updated.author
  item.value.description = updated.description
  item.value.clearance = updated.clearance
  if (updated.title) document.title = updated.title
}

const loadFavorites = async () => {
  try {
    const res = await api.get('/favorites')
    const ids = res.data.items.map((f: any) => f.hash_id)
    favoriteIds.value = new Set(ids)
  } catch (err) {
    console.error('Failed to load favorites', err)
  }
}

const toggleFavorite = async () => {
  if (!item.value || !item.value.hash_id) return
  const id = item.value.hash_id
  try {
    const newIds = new Set(favoriteIds.value)
    if (favoriteIds.value.has(id)) {
      await api.delete(`/favorites/${encodeURIComponent(id)}`)
      newIds.delete(id)
    } else {
      await api.post('/favorites', { hash_id: id })
      newIds.add(id)
    }
    favoriteIds.value = newIds
  } catch (err) {
    console.error('Failed to toggle favorite', err)
  }
}

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
  loadFavorites()
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
const isEpub = computed(() => fileExtension.value === 'epub')
const isFb2 = computed(() => {
  const n = (item.value?.name || '').toLowerCase()
  return n.endsWith('.fb2') || n.endsWith('.fb2.zip')
})
const isMd = computed(() => ['md', 'markdown'].includes(fileExtension.value))
const isTxt = computed(() => fileExtension.value === 'txt')
const isHtml = computed(() => {
  const n = (item.value?.name || '').toLowerCase()
  return n.endsWith('.html') || n.endsWith('.htm')
      || n.endsWith('.html.zip') || n.endsWith('.htm.zip')
})

const displayFormat = computed(() => {
  if (isFb2.value) return 'FB2'
  if (isMd.value) return 'Markdown'
  if (isHtml.value) return 'HTML'
  return fileExtension.value
})

const fb2Meta = ref<{ title: string; authors: string[]; annotation_html: string } | null>(null)

const loadFb2Meta = async (path: string) => {
  fb2Meta.value = null
  try {
    const res = await api.get('/fb2-metadata', { params: { path } })
    fb2Meta.value = res.data
  } catch (e) {
    console.error('Failed to load FB2 metadata', e)
  }
}

watch(
  () => item.value && isFb2.value ? item.value.path : null,
  (p) => { if (p) loadFb2Meta(p); else fb2Meta.value = null },
  { immediate: true }
)

const textPreview = ref<{ text: string; html: string }>({ text: '', html: '' })

const loadTextPreview = async (path: string) => {
  textPreview.value = { text: '', html: '' }
  try {
    const res = await api.get('/text-preview', { params: { path, max_chars: 1500 } })
    textPreview.value = {
      text: res.data.text || '',
      html: res.data.html || '',
    }
  } catch (e) {
    console.error('Failed to load text preview', e)
  }
}

watch(
  () => item.value && (isMd.value || isTxt.value) ? item.value.path : null,
  (p) => { if (p) loadTextPreview(p); else textPreview.value = { text: '', html: '' } },
  { immediate: true }
)

const htmlPreview = ref<{ html: string }>({ html: '' })

const loadHtmlPreview = async (path: string) => {
  htmlPreview.value = { html: '' }
  try {
    const res = await api.get('/html-preview', { params: { path, max_chars: 1500 } })
    htmlPreview.value = { html: res.data.html || '' }
  } catch (e) {
    console.error('Failed to load HTML preview', e)
  }
}

watch(
  () => item.value && isHtml.value ? item.value.path : null,
  (p) => { if (p) loadHtmlPreview(p); else htmlPreview.value = { html: '' } },
  { immediate: true }
)
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
          <div class="w-full max-w-sm aspect-[3/4] rounded-lg shadow-xl overflow-hidden bg-white dark:bg-gray-800 flex items-center justify-center border border-gray-200 dark:border-gray-700 relative">
            <img v-if="item.cover_url" :src="getFullUrl(item.cover_url)" :alt="item.name" class="w-full h-full object-contain" />
            <template v-else-if="(isMd || isTxt) && (textPreview.html || textPreview.text)">
              <div
                v-if="textPreview.html"
                class="md-content md-content--preview absolute inset-0 m-0 px-3 py-3 text-[10px] leading-snug overflow-hidden text-gray-700 dark:text-gray-300"
                v-html="textPreview.html"
              ></div>
              <pre
                v-else
                class="absolute inset-0 m-0 px-3 py-3 text-[10px] leading-snug whitespace-pre-wrap break-words overflow-hidden text-gray-700 dark:text-gray-300 font-serif"
              >{{ textPreview.text }}</pre>
              <div class="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-white dark:from-gray-800 to-transparent pointer-events-none"></div>
            </template>
            <template v-else-if="isHtml && htmlPreview.html">
              <div
                class="html-content html-content--preview absolute inset-0 m-0 px-3 py-3 text-[10px] leading-snug overflow-hidden text-gray-700 dark:text-gray-300"
                v-html="htmlPreview.html"
              ></div>
              <div class="absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-white dark:from-gray-800 to-transparent pointer-events-none"></div>
            </template>
            <DocumentIcon v-else class="w-32 h-32 text-gray-300 dark:text-gray-600" />
          </div>
        </div>
        
        <!-- Right Column: Metadata & Actions -->
        <div class="md:col-span-2 flex flex-col justify-center space-y-6">
          <div>
            <div class="flex items-start justify-between gap-4">
              <h1 class="text-2xl md:text-4xl font-serif font-bold text-gray-900 dark:text-gray-100 break-words leading-tight">
                {{ item.title || item.name.replace(/\.[^/.]+$/, "") }}
              </h1>
              <div class="flex items-center gap-1 flex-shrink-0 mt-1">
                <button
                  v-if="currentUser?.is_admin && item.hash_id"
                  @click.prevent="openEditor()"
                  class="p-2 rounded-full text-gray-400 hover:text-blue-500 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                  :title="t('admin.edit_book')"
                >
                  <PencilSquareIcon class="h-7 w-7" />
                </button>
                <button v-if="item.hash_id" @click.prevent="toggleFavorite()" class="p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors" :class="{ 'text-blue-500': favoriteIds.has(item.hash_id), 'text-gray-400 hover:text-blue-500': !favoriteIds.has(item.hash_id) }" :title="favoriteIds.has(item.hash_id) ? t('app.remove_favorite') : t('app.add_favorite')">
                  <BookmarkIconSolid v-if="favoriteIds.has(item.hash_id)" class="h-7 w-7" />
                  <BookmarkIcon v-else class="h-7 w-7" />
                </button>
              </div>
            </div>
            <h2 v-if="item.author" class="mt-2 text-xl md:text-2xl text-gray-700 dark:text-gray-300 font-medium">
              {{ item.author }}
            </h2>
            <p class="mt-2 text-sm text-gray-500 dark:text-gray-400 font-sans break-all">
              {{ item.name }}
            </p>
          </div>
          
          <!-- Metadata Table -->
          <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm">
            <table class="w-full text-sm text-left">
              <tbody class="divide-y divide-gray-200 dark:divide-gray-700">
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.format') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400 uppercase font-semibold">{{ displayFormat }}</td>
                </tr>
                <tr v-if="fb2Meta?.title" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.book_title') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400">{{ fb2Meta.title }}</td>
                </tr>
                <tr v-if="fb2Meta?.authors?.length" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.author') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400">{{ fb2Meta.authors.join(', ') }}</td>
                </tr>
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.size') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400">{{ formatBytes(item.size, 0) }} ({{ item.size }} bytes)</td>
                </tr>
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.modified') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400">{{ formatDate(item.mtime) }}</td>
                </tr>
                <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">{{ t('app.location') }}</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400">
                    <router-link :to="`/browse/${currentPath.split('/').slice(0, -1).join('/')}`" class="hover:text-blue-600 dark:hover:text-blue-400 hover:underline">
                      /{{ currentPath.split('/').slice(0, -1).join('/') || 'Root' }}
                    </router-link>
                  </td>
                </tr>
                <tr v-if="item.description" class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                  <th scope="row" class="px-6 py-4 font-medium text-gray-900 dark:text-gray-100">Description</th>
                  <td class="px-6 py-4 text-gray-600 dark:text-gray-400 prose dark:prose-invert max-w-none text-sm whitespace-pre-wrap" v-html="item.description"></td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- FB2 Annotation -->
          <details v-if="fb2Meta?.annotation_html" class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm group">
            <summary class="px-6 py-4 cursor-pointer font-medium text-gray-900 dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-700/50 list-none flex items-center justify-between rounded-lg">
              <span>{{ t('app.annotation') }}</span>
              <span class="transition-transform group-open:rotate-90 text-gray-400">›</span>
            </summary>
            <div class="px-6 pb-5 prose dark:prose-invert max-w-none text-gray-700 dark:text-gray-300 fb2-content" v-html="fb2Meta.annotation_html"></div>
          </details>

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
          <ImageViewer v-else-if="isImage" :src="getDownloadUrl()" />
          
          <!-- PDF Viewer (pdfjs-dist) -->
          <PdfViewer v-else-if="isPdf" :path="item.path" :hash-id="item.hash_id" />

          <!-- DjVu Viewer -->
          <DjvuViewer v-else-if="isDjvu" :path="item.path" :hash-id="item.hash_id" />

          <!-- EPUB Viewer -->
          <EpubViewer v-else-if="isEpub" :path="item.path" :hash-id="item.hash_id" />

          <!-- FB2 Viewer (also handles .fb2.zip) -->
          <Fb2Viewer v-else-if="isFb2" :path="item.path" :hash-id="item.hash_id" />

          <!-- Markdown / plain text viewer -->
          <MdViewer v-else-if="isMd || isTxt" :path="item.path" :hash-id="item.hash_id" />

          <!-- HTML Viewer (also handles .html.zip) -->
          <HtmlViewer v-else-if="isHtml" :path="item.path" :hash-id="item.hash_id" />

          <!-- Unsupported -->
          <div v-else class="text-center p-8">
            <DocumentIcon class="mx-auto h-16 w-16 text-gray-400 mb-4" />
            <p class="text-lg">{{ t('app.preview_not_available') }}</p>
            <p class="text-sm mt-2 text-gray-500">{{ t('app.please_download') }}</p>
          </div>
          
        </div>
      </div>

    </div>

    <BookMetadataEditor :hash-id="editingId" @close="editingId = null" @saved="onEditorSaved" />
  </div>
</template>