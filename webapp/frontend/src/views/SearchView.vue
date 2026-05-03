<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import { DocumentIcon, FolderIcon, MagnifyingGlassIcon } from '@heroicons/vue/24/outline'

const route = useRoute()
const matches = ref<any[]>([])
const loading = ref(false)
const searched = ref(false)
const error = ref('')

const doSearch = async (q: string) => {
  if (!q) {
    matches.value = []
    searched.value = false
    return
  }
  
  loading.value = true
  error.value = ''
  searched.value = true
  
  try {
    const res = await api.get('/search', { params: { q } })
    matches.value = res.data.matches
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  doSearch(route.query.q as string)
})

watch(() => route.query.q, (newQ) => {
  doSearch(newQ as string)
})

const getFullUrl = (url: string) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}
</script>

<template>
  <div class="space-y-6">
    <div class="bg-white p-6 rounded-lg shadow-sm border border-gray-100">
      <h1 class="text-2xl font-bold text-gray-900 mb-2 flex items-center gap-2">
        <MagnifyingGlassIcon class="h-6 w-6 text-blue-600" />
        Search Results
      </h1>
      <p class="text-gray-500">
        Results for <span class="font-semibold text-gray-900">"{{ route.query.q }}"</span>
      </p>
    </div>

    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
    </div>
    
    <div v-else-if="error" class="bg-red-50 text-red-600 p-4 rounded-lg border border-red-200">
      {{ error }}
    </div>

    <div v-else-if="searched && matches.length === 0" class="text-center py-20 text-gray-500 bg-white rounded-lg border border-gray-100 shadow-sm">
      <MagnifyingGlassIcon class="mx-auto h-12 w-12 text-gray-300 mb-3" />
      <p class="text-lg">No matches found.</p>
    </div>

    <div v-else-if="matches.length > 0" class="bg-white rounded-lg shadow-sm border border-gray-100 overflow-hidden">
      <div class="px-4 py-3 bg-gray-50 border-b border-gray-100 text-sm text-gray-500 font-medium">
        Found {{ matches.length }} matches (limited to 100)
      </div>
      <ul class="divide-y divide-gray-100">
        <li v-for="match in matches" :key="match.path" class="hover:bg-gray-50 transition-colors p-4">
          <div class="flex gap-4">
            <!-- Icon/Cover -->
            <div class="flex-shrink-0">
               <div v-if="match.is_dir" class="h-12 w-12 flex items-center justify-center bg-blue-50 rounded-lg">
                 <FolderIcon class="h-8 w-8 text-blue-400" />
               </div>
               <div v-else class="h-16 w-12 flex items-center justify-center bg-gray-100 rounded shadow-sm overflow-hidden border border-gray-200">
                 <img v-if="match.cover_url" :src="getFullUrl(match.cover_url)" class="w-full h-full object-cover" />
                 <DocumentIcon v-else class="h-6 w-6 text-gray-400" />
               </div>
            </div>
            
            <!-- Details -->
            <div class="flex-1 min-w-0">
              <div class="flex items-start justify-between">
                <div>
                  <a v-if="!match.is_dir" :href="getFullUrl(`/api/files/${match.path.split('/').map(encodeURIComponent).join('/')}`)" target="_blank" class="text-lg font-medium text-blue-600 hover:underline break-words">
                    {{ match.name }}
                  </a>
                  <template v-else>
                    <a v-if="match.path.startsWith('Websites/')" :href="getFullUrl(`/api/files/${match.path.split('/').map(encodeURIComponent).join('/')}/`)" target="_blank" class="text-lg font-medium text-blue-600 hover:underline break-words">
                      {{ match.name }}
                    </a>
                    <router-link v-else :to="`/browse/${match.path}`" class="text-lg font-medium text-blue-600 hover:underline break-words">
                      {{ match.name }}
                    </router-link>
                  </template>
                  <p v-if="match.description" class="text-sm text-gray-600 mt-1 line-clamp-2" v-html="match.description.replace(new RegExp(route.query.q as string, 'gi'), (m: string) => `<mark class='bg-yellow-200'>${m}</mark>`)"></p>
                </div>
              </div>
              
              <div class="mt-2 text-xs text-gray-400 flex items-center gap-1">
                 Location: 
                 <router-link :to="`/browse/${match.parent_dir}`" class="hover:text-blue-500 hover:underline">
                   /{{ match.parent_dir || 'Root' }}
                 </router-link>
              </div>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>