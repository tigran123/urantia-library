<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import api from '../api'
import { userInitials } from '../userDisplay'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps<{
  user: { email: string, avatar_url?: string, real_name?: string | null, search_per_page?: number | null } | null,
  isOpen: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update-user', user: any): void
}>()

const activeTab = ref('Avatar')
const selectedFile = ref<File | null>(null)
const previewUrl = ref<string | null>(null)
const isUploading = ref(false)

const tabs = computed(() => [
  { id: 'Personal Information', label: t('settings.personal_info') },
  { id: 'Avatar', label: t('settings.avatar') },
  { id: 'Search', label: t('settings.search') }
])

const PER_PAGE_OPTIONS = [10, 25, 50, 100, 200]
const searchPerPage = ref<number>(50)
const isSavingSearch = ref(false)

const realName = ref('')
const isSavingPersonalInfo = ref(false)

watch(
  () => [props.isOpen, props.user?.search_per_page, props.user?.real_name],
  () => {
    searchPerPage.value = props.user?.search_per_page ?? 50
    realName.value = props.user?.real_name ?? ''
  },
  { immediate: true }
)

const savePersonalInfo = async () => {
  isSavingPersonalInfo.value = true
  try {
    const response = await api.put('/users/me/settings', { real_name: realName.value.trim() })
    emit('update-user', response.data)
    emit('close')
  } catch (error) {
    console.error('Failed to save personal information', error)
    alert('Failed to save settings')
  } finally {
    isSavingPersonalInfo.value = false
  }
}

const saveSearchSettings = async () => {
  isSavingSearch.value = true
  try {
    const response = await api.put('/users/me/settings', { search_per_page: searchPerPage.value })
    emit('update-user', response.data)
    emit('close')
  } catch (error) {
    console.error('Failed to save search settings', error)
    alert('Failed to save settings')
  } finally {
    isSavingSearch.value = false
  }
}

const handleFileChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    selectedFile.value = target.files[0]
    previewUrl.value = URL.createObjectURL(selectedFile.value)
  }
}

const getFullUrl = (url: string | undefined) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}

const uploadAvatar = async () => {
  if (!selectedFile.value) return
  isUploading.value = true
  const formData = new FormData()
  formData.append('file', selectedFile.value)
  try {
    const response = await api.post('/users/me/avatar', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
    emit('update-user', response.data)
    emit('close')
  } catch (error) {
    console.error('Failed to upload avatar', error)
    alert('Failed to upload avatar')
  } finally {
    isUploading.value = false
    selectedFile.value = null
    previewUrl.value = null
  }
}

const close = () => {
  emit('close')
  selectedFile.value = null
  previewUrl.value = null
}
</script>

<template>
  <div v-if="isOpen" class="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-0">
    <div class="fixed inset-0 transition-opacity" style="background-color: rgba(0, 0, 0, 0.2);" @click="close"></div>
    
    <div class="relative bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-2xl overflow-hidden z-10 text-left flex flex-col max-h-[90vh]">
      <div class="flex justify-between items-center p-4 border-b border-gray-200 dark:border-gray-700">
        <h3 class="text-lg font-medium text-gray-900 dark:text-white">{{ t('settings.title') }}</h3>
        <button @click="close" class="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300">
          <XMarkIcon class="h-6 w-6" />
        </button>
      </div>
      
      <div class="flex flex-1 overflow-hidden">
        <!-- Sidebar -->
        <div class="w-1/3 border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 p-4 overflow-y-auto">
          <nav class="space-y-1">
            <button
              v-for="tab in tabs"
              :key="tab.id"
              @click="activeTab = tab.id"
              :class="[
                activeTab === tab.id ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-200' : 'text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700/50',
                'group flex items-center px-3 py-2 text-sm font-medium rounded-md w-full text-left'
              ]"
            >
              {{ tab.label }}
            </button>
          </nav>
        </div>
        
        <!-- Content -->
        <div class="w-2/3 p-6 overflow-y-auto">
          <div v-if="activeTab === 'Personal Information'">
            <h4 class="text-md font-medium text-gray-900 dark:text-white mb-4">{{ t('settings.personal_info') }}</h4>
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">{{ t('settings.email') }} {{ user?.email }}</p>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              {{ t('settings.full_name') }}
            </label>
            <input
              v-model="realName"
              type="text"
              maxlength="100"
              autocomplete="name"
              class="block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            />
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-2">{{ t('settings.full_name_help') }}</p>
            <div class="mt-6 flex justify-end">
              <button
                @click="savePersonalInfo"
                :disabled="isSavingPersonalInfo"
                class="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded hover:bg-blue-700 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {{ isSavingPersonalInfo ? t('settings.saving') : t('settings.save') }}
              </button>
            </div>
          </div>
          
          <div v-if="activeTab === 'Search'">
            <h4 class="text-md font-medium text-gray-900 dark:text-white mb-4">{{ t('settings.search') }}</h4>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              {{ t('settings.results_per_page') }}
            </label>
            <select
              v-model.number="searchPerPage"
              class="block w-48 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
            >
              <option v-for="n in PER_PAGE_OPTIONS" :key="n" :value="n">{{ n }}</option>
            </select>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-2">{{ t('settings.results_per_page_help') }}</p>
            <div class="mt-6 flex justify-end">
              <button
                @click="saveSearchSettings"
                :disabled="isSavingSearch"
                class="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded hover:bg-blue-700 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {{ isSavingSearch ? t('settings.saving') : t('settings.save') }}
              </button>
            </div>
          </div>

          <div v-if="activeTab === 'Avatar'">
            <h4 class="text-md font-medium text-gray-900 dark:text-white mb-4">{{ t('settings.profile_picture') }}</h4>
            <div class="flex items-center space-x-6">
              <div class="shrink-0">
                <img
                  v-if="previewUrl || user?.avatar_url"
                  :src="previewUrl || getFullUrl(user?.avatar_url)"
                  class="h-24 w-24 object-cover rounded-full border border-gray-200 dark:border-gray-700"
                  alt="Avatar"
                />
                <div v-else class="h-24 w-24 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center">
                  <span class="text-gray-500 dark:text-gray-300 text-2xl font-semibold">{{ userInitials(user) }}</span>
                </div>
              </div>
              <label class="block">
                <span class="sr-only">{{ t('settings.choose_photo') }}</span>
                <input
                  type="file"
                  @change="handleFileChange"
                  accept="image/png, image/jpeg, image/gif, image/webp"
                  class="block w-full text-sm text-gray-500 dark:text-gray-400
                    file:mr-4 file:py-2 file:px-4
                    file:rounded-md file:border-0
                    file:text-sm file:font-semibold
                    file:bg-blue-50 file:text-blue-700
                    hover:file:bg-blue-100
                    dark:file:bg-blue-900/30 dark:file:text-blue-300
                    dark:hover:file:bg-blue-900/50
                    cursor-pointer"
                />
              </label>
            </div>
            <div class="mt-6 flex justify-end">
              <button
                @click="uploadAvatar"
                :disabled="!selectedFile || isUploading"
                class="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded hover:bg-blue-700 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {{ isUploading ? t('settings.uploading') : t('settings.save_avatar') }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
