<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { MagnifyingGlassIcon, BookOpenIcon } from '@heroicons/vue/24/outline'

const searchQuery = ref('')
const router = useRouter()

const performSearch = () => {
  if (searchQuery.value.trim()) {
    router.push({ name: 'search', query: { q: searchQuery.value } })
  }
}
</script>

<template>
  <div class="min-h-screen flex flex-col">
    <!-- Header -->
    <header class="bg-white shadow-sm sticky top-0 z-10">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="flex justify-between h-16 items-center">
          <div class="flex items-center">
            <router-link to="/" class="flex items-center gap-2 text-xl font-bold text-gray-900">
              <BookOpenIcon class="h-8 w-8 text-blue-600" />
              Urantia Library
            </router-link>
          </div>
          
          <div class="flex-1 max-w-xl px-8 hidden sm:block">
            <form @submit.prevent="performSearch" class="relative">
              <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <MagnifyingGlassIcon class="h-5 w-5 text-gray-400" />
              </div>
              <input 
                v-model="searchQuery"
                type="search" 
                class="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-md leading-5 bg-white placeholder-gray-500 focus:outline-none focus:placeholder-gray-400 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm" 
                placeholder="Search library..." 
              />
            </form>
          </div>
        </div>
      </div>
      <!-- Mobile search -->
      <div class="sm:hidden px-4 pb-3">
        <form @submit.prevent="performSearch" class="relative">
          <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
            <MagnifyingGlassIcon class="h-5 w-5 text-gray-400" />
          </div>
          <input 
            v-model="searchQuery"
            type="search" 
            class="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-md leading-5 bg-white placeholder-gray-500 focus:outline-none focus:placeholder-gray-400 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm" 
            placeholder="Search library..." 
          />
        </form>
      </div>
    </header>

    <!-- Main Content -->
    <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <router-view />
    </main>
    
    <footer class="bg-white border-t py-6 text-center text-sm text-gray-500 mt-auto">
      &copy; 2026 Urantia Library
    </footer>
  </div>
</template>