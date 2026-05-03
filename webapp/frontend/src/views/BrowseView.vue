<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import { FolderIcon, DocumentIcon, HomeIcon, ChevronRightIcon, Squares2X2Icon, ListBulletIcon } from '@heroicons/vue/24/outline'

const route = useRoute()
const items = ref<any[]>([])
const loading = ref(true)
const error = ref('')
const currentPath = ref('')
const viewMode = ref<'grid' | 'list'>('grid')

const formatBytes = (bytes: number, decimals = 2) => {
    if (!+bytes) return '0 Bytes'
    const k = 1024
    const dm = decimals < 0 ? 0 : decimals
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const loadPath = async (path: string) => {
  loading.value = true
  error.value = ''
  try {
    const p = Array.isArray(path) ? path.join('/') : path || ''
    currentPath.value = p
    const res = await api.get('/browse', { params: { path: p } })
    items.value = res.data.items
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadPath(route.params.path as string)
})

watch(() => route.params.path, (newPath) => {
  loadPath(newPath as string)
})

const getBreadcrumbs = () => {
  if (!currentPath.value) return []
  const parts = currentPath.value.split('/').filter(Boolean)
  return parts.map((part, index) => {
    return {
      name: part,
      path: '/' + parts.slice(0, index + 1).join('/')
    }
  })
}

const getFullUrl = (url: string) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}
</script>

<template>
  <div class="space-y-6">
    <!-- Toolbar -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white p-4 rounded-lg shadow-sm border border-gray-100">
      
      <!-- Breadcrumbs -->
      <nav class="flex text-sm font-medium text-gray-500 overflow-x-auto" aria-label="Breadcrumb">
        <ol class="inline-flex items-center space-x-1 md:space-x-3 whitespace-nowrap">
          <li class="inline-flex items-center">
            <router-link to="/browse" class="inline-flex items-center hover:text-blue-600 transition-colors">
              <HomeIcon class="h-4 w-4 mr-2" />
              Root
            </router-link>
          </li>
          <li v-for="crumb in getBreadcrumbs()" :key="crumb.path">
            <div class="flex items-center">
              <ChevronRightIcon class="h-4 w-4 text-gray-400 mx-1" />
              <router-link :to="`/browse${crumb.path}`" class="hover:text-blue-600 transition-colors">
                {{ crumb.name }}
              </router-link>
            </div>
          </li>
        </ol>
      </nav>

      <!-- View Toggle -->
      <div class="flex bg-gray-100 rounded-lg p-1 self-start sm:self-auto">
        <button 
          @click="viewMode = 'grid'" 
          :class="['p-1.5 rounded-md transition-colors', viewMode === 'grid' ? 'bg-white shadow-sm text-blue-600' : 'text-gray-500 hover:text-gray-700']"
          title="Grid View"
        >
          <Squares2X2Icon class="h-5 w-5" />
        </button>
        <button 
          @click="viewMode = 'list'" 
          :class="['p-1.5 rounded-md transition-colors', viewMode === 'list' ? 'bg-white shadow-sm text-blue-600' : 'text-gray-500 hover:text-gray-700']"
          title="List View"
        >
          <ListBulletIcon class="h-5 w-5" />
        </button>
      </div>
    </div>

    <!-- Content -->
    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
    </div>
    
    <div v-else-if="error" class="bg-red-50 text-red-600 p-4 rounded-lg border border-red-200">
      {{ error }}
    </div>

    <div v-else-if="items.length === 0" class="text-center py-20 text-gray-500 bg-white rounded-lg border border-gray-100 shadow-sm">
      <FolderIcon class="mx-auto h-12 w-12 text-gray-300 mb-3" />
      <p class="text-lg">This folder is empty</p>
    </div>

    <template v-else>
      <!-- Grid View -->
      <div v-if="viewMode === 'grid'" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-6">
        <template v-for="item in items" :key="item.name">
          <template v-if="item.is_dir">
            <a v-if="currentPath.startsWith('Websites')" :href="getFullUrl(`/api/files/${item.path.split('/').map(encodeURIComponent).join('/')}/`)" target="_blank" class="group flex flex-col items-center p-4 bg-white rounded-xl shadow-sm border border-gray-100 hover:shadow-md transition-all hover:border-blue-300">
              <div class="h-32 flex items-center justify-center w-full bg-blue-50/50 rounded-lg mb-3 group-hover:bg-blue-50 transition-colors">
                <FolderIcon class="h-16 w-16 text-blue-400 group-hover:text-blue-500" />
              </div>
              <h3 class="text-sm font-medium text-gray-900 text-center line-clamp-2 w-full break-words" :title="item.name">{{ item.name }}</h3>
              <p v-if="item.description" class="text-xs text-gray-500 mt-1 text-center line-clamp-2" :title="item.description" v-html="item.description"></p>
            </a>
            <router-link v-else :to="`/browse/${currentPath ? currentPath + '/' : ''}${item.name}`" class="group flex flex-col items-center p-4 bg-white rounded-xl shadow-sm border border-gray-100 hover:shadow-md transition-all hover:border-blue-300">
              <div class="h-32 flex items-center justify-center w-full bg-blue-50/50 rounded-lg mb-3 group-hover:bg-blue-50 transition-colors">
                <FolderIcon class="h-16 w-16 text-blue-400 group-hover:text-blue-500" />
              </div>
              <h3 class="text-sm font-medium text-gray-900 text-center line-clamp-2 w-full break-words" :title="item.name">{{ item.name }}</h3>
              <p v-if="item.description" class="text-xs text-gray-500 mt-1 text-center line-clamp-2" :title="item.description" v-html="item.description"></p>
            </router-link>
          </template>
          
          <a v-else :href="getFullUrl(`/api/files/${item.path.split('/').map(encodeURIComponent).join('/')}`)" target="_blank" class="group flex flex-col items-center p-4 bg-white rounded-xl shadow-sm border border-gray-100 hover:shadow-md transition-all hover:border-green-300">
            <div class="h-40 w-full mb-3 rounded-lg overflow-hidden flex items-center justify-center bg-gray-50">
              <img v-if="item.cover_url" :src="getFullUrl(item.cover_url)" :alt="item.name" class="w-full h-full object-cover object-top group-hover:scale-105 transition-transform duration-300" />
              <DocumentIcon v-else class="h-16 w-16 text-gray-300" />
            </div>
            <h3 class="text-sm font-medium text-gray-900 text-center line-clamp-2 w-full break-words" :title="item.name">{{ item.name }}</h3>
            <p v-if="item.description" class="text-xs text-gray-500 mt-1 text-center line-clamp-2" :title="item.description" v-html="item.description"></p>
          </a>
        </template>
      </div>

      <!-- List View -->
      <div v-else class="bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden">
        <ul class="divide-y divide-gray-100">
          <li v-for="item in items" :key="item.name" class="hover:bg-gray-50 transition-colors">
            <template v-if="item.is_dir">
              <a v-if="currentPath.startsWith('Websites')" :href="getFullUrl(`/api/files/${item.path.split('/').map(encodeURIComponent).join('/')}/`)" target="_blank" class="flex items-center p-4">
                <FolderIcon class="h-8 w-8 text-blue-400 flex-shrink-0 mr-4" />
                <div class="flex-1 min-w-0">
                  <p class="text-sm font-medium text-gray-900 truncate">{{ item.name }}</p>
                  <p v-if="item.description" class="text-xs text-gray-500 truncate mt-0.5" v-html="item.description"></p>
                </div>
              </a>
              <router-link v-else :to="`/browse/${currentPath ? currentPath + '/' : ''}${item.name}`" class="flex items-center p-4">
                <FolderIcon class="h-8 w-8 text-blue-400 flex-shrink-0 mr-4" />
                <div class="flex-1 min-w-0">
                  <p class="text-sm font-medium text-gray-900 truncate">{{ item.name }}</p>
                  <p v-if="item.description" class="text-xs text-gray-500 truncate mt-0.5" v-html="item.description"></p>
                </div>
              </router-link>
            </template>
            
            <a v-else :href="getFullUrl(`/api/files/${item.path.split('/').map(encodeURIComponent).join('/')}`)" target="_blank" class="flex items-center p-4">
              <div class="h-12 w-10 flex-shrink-0 mr-4 rounded bg-gray-100 flex items-center justify-center overflow-hidden">
                <img v-if="item.cover_url" :src="getFullUrl(item.cover_url)" class="w-full h-full object-cover" />
                <DocumentIcon v-else class="h-6 w-6 text-gray-400" />
              </div>
              <div class="flex-1 min-w-0">
                <p class="text-sm font-medium text-gray-900 truncate">{{ item.name }}</p>
                <p v-if="item.description" class="text-xs text-gray-500 truncate mt-0.5" v-html="item.description"></p>
              </div>
              <div class="text-xs text-gray-400 ml-4 whitespace-nowrap">
                {{ formatBytes(item.size) }}
              </div>
            </a>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>