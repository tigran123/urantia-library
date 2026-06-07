<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { XMarkIcon } from '@heroicons/vue/24/outline'
import api, { getMyNotificationPrefs, setMyNotificationPrefs, type NotificationPrefs } from '../api'
import { userInitials } from '../userDisplay'
import { useI18n } from 'vue-i18n'
import { gridItemSize, type GridItemSize } from '../composables/useGridItemSize'
import { textSize, TEXT_SIZE_PX, type TextSize } from '../composables/useTextSize'
import AvatarCropper from './AvatarCropper.vue'

const { t } = useI18n()

const props = defineProps<{
  user: { email: string, avatar_url?: string | null, real_name?: string | null, search_per_page?: number | null } | null,
  isOpen: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update-user', user: any): void
}>()

const activeTab = ref('Avatar')
const selectedFile = ref<File | null>(null)
const isUploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const tabs = computed(() => [
  { id: 'Personal Information', label: t('settings.personal_info') },
  { id: 'Avatar', label: t('settings.avatar') },
  { id: 'Appearance', label: t('settings.appearance') },
  { id: 'Search', label: t('settings.search') },
  { id: 'Notifications', label: t('feedback.notif.section') },
])

const GRID_SIZE_OPTIONS: { id: GridItemSize; cols: string; count: number }[] = [
  { id: 'small',  cols: 'grid-cols-6', count: 6 },
  { id: 'normal', cols: 'grid-cols-4', count: 4 },
  { id: 'large',  cols: 'grid-cols-3', count: 3 },
]

const TEXT_SIZE_OPTIONS: { id: TextSize; px: string }[] = [
  { id: 'small',  px: TEXT_SIZE_PX.small },
  { id: 'normal', px: TEXT_SIZE_PX.normal },
  { id: 'large',  px: TEXT_SIZE_PX.large },
  { id: 'xlarge', px: TEXT_SIZE_PX.xlarge },
]

const notifPrefs = ref<NotificationPrefs>({
  email_on_reply: true, email_on_status: true, email_weekly_summary: false,
})
const isSavingNotif = ref(false)
const notifLoaded = ref(false)

async function loadNotifPrefs() {
  if (notifLoaded.value) return
  try {
    const r = await getMyNotificationPrefs()
    notifPrefs.value = r.data
  } finally {
    notifLoaded.value = true
  }
}

async function saveNotifPrefs() {
  isSavingNotif.value = true
  try {
    await setMyNotificationPrefs(notifPrefs.value)
    emit('close')
  } catch {
    alert('Failed to save settings')
  } finally {
    isSavingNotif.value = false
  }
}

watch(activeTab, (v) => {
  if (v === 'Notifications') loadNotifPrefs()
})

watch(() => props.isOpen, (open) => {
  if (open && activeTab.value === 'Notifications') loadNotifPrefs()
  if (!open) notifLoaded.value = false
})

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
    selectedFile.value = target.files[0]  // entering crop mode — the template shows AvatarCropper
  }
  // Reset so picking the *same* file again still fires @change.
  target.value = ''
}

const getFullUrl = (url: string | undefined) => {
  if (!url) return ''
  return api.defaults.baseURL?.replace('/api', '') + url
}

// The cropper emits a finished square JPEG Blob; upload it to the existing
// endpoint (the backend re-encodes + normalizes server-side).
const onCropped = async (blob: Blob) => {
  if (isUploading.value) return
  isUploading.value = true
  const formData = new FormData()
  formData.append('file', blob, 'avatar.jpg')
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
  }
}

const resetSelection = () => { selectedFile.value = null }

// Delete the profile photo: clears avatar_url server-side (and removes the file
// on disk), so the UI falls back to the initials. Stays in the modal.
const isRemoving = ref(false)
const removeAvatar = async () => {
  if (isRemoving.value) return
  isRemoving.value = true
  try {
    const response = await api.delete('/users/me/avatar')
    emit('update-user', response.data)
  } catch (error) {
    console.error('Failed to remove avatar', error)
    alert('Failed to remove avatar')
  } finally {
    isRemoving.value = false
  }
}

const close = () => {
  emit('close')
  selectedFile.value = null
}

// Esc closes the dialog, matching the X button and backdrop click. The
// component is always mounted (the inner v-if="isOpen" controls visibility),
// so we gate the window listener on props.isOpen.
const onKeydown = (e: KeyboardEvent) => {
  if (props.isOpen && e.key === 'Escape') close()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
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

          <div v-if="activeTab === 'Appearance'">
            <h4 class="text-md font-medium text-gray-900 dark:text-white mb-4">{{ t('settings.appearance') }}</h4>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">
              {{ t('settings.grid_item_size') }}
            </label>
            <div class="space-y-3">
              <label
                v-for="opt in GRID_SIZE_OPTIONS"
                :key="opt.id"
                :class="[
                  'flex items-center gap-4 p-3 border rounded-lg cursor-pointer transition-colors',
                  gridItemSize === opt.id
                    ? 'border-blue-500 ring-2 ring-blue-500 bg-blue-50 dark:bg-blue-900/20'
                    : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                ]"
              >
                <input
                  type="radio"
                  :value="opt.id"
                  v-model="gridItemSize"
                  class="h-4 w-4 text-blue-600 border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
                <span class="w-16 shrink-0 text-sm font-medium text-gray-900 dark:text-gray-100">
                  {{ t(`settings.grid_size.${opt.id}`) }}
                </span>
                <div :class="['grid gap-1 flex-1 max-w-[220px]', opt.cols]" aria-hidden="true">
                  <div
                    v-for="i in opt.count"
                    :key="i"
                    class="aspect-[3/4] bg-gray-300 dark:bg-gray-600 rounded-sm"
                  ></div>
                </div>
              </label>
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-3">{{ t('settings.grid_item_size_help') }}</p>

            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-3 mt-8">
              {{ t('settings.text_size') }}
            </label>
            <div class="space-y-3">
              <label
                v-for="opt in TEXT_SIZE_OPTIONS"
                :key="opt.id"
                :class="[
                  'flex items-center gap-4 p-3 border rounded-lg cursor-pointer transition-colors',
                  textSize === opt.id
                    ? 'border-blue-500 ring-2 ring-blue-500 bg-blue-50 dark:bg-blue-900/20'
                    : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                ]"
              >
                <input
                  type="radio"
                  :value="opt.id"
                  v-model="textSize"
                  class="h-4 w-4 text-blue-600 border-gray-300 dark:border-gray-600 focus:ring-blue-500"
                />
                <span class="w-24 shrink-0 text-sm font-medium text-gray-900 dark:text-gray-100">
                  {{ t(`settings.text_size_opts.${opt.id}`) }}
                </span>
                <span
                  class="flex-1 font-medium text-gray-700 dark:text-gray-200 leading-none"
                  :style="{ fontSize: opt.px }"
                  aria-hidden="true"
                >Aa</span>
              </label>
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-3">{{ t('settings.text_size_help') }}</p>
          </div>

          <div v-if="activeTab === 'Notifications'">
            <h4 class="text-md font-medium text-gray-900 dark:text-white mb-4">{{ t('feedback.notif.section') }}</h4>
            <div class="space-y-4">
              <label class="flex items-start gap-3 cursor-pointer">
                <input
                  v-model="notifPrefs.email_on_reply"
                  type="checkbox"
                  class="mt-0.5 h-4 w-4 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
                  :aria-label="t('feedback.notif.reply')"
                />
                <span class="flex-1">
                  <span class="block text-sm font-medium text-gray-900 dark:text-gray-100">{{ t('feedback.notif.reply') }}</span>
                  <span class="block text-xs text-gray-500 dark:text-gray-400">{{ t('feedback.notif.reply_hint') }}</span>
                </span>
              </label>

              <label class="flex items-start gap-3 cursor-pointer">
                <input
                  v-model="notifPrefs.email_on_status"
                  type="checkbox"
                  class="mt-0.5 h-4 w-4 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
                  :aria-label="t('feedback.notif.status')"
                />
                <span class="flex-1">
                  <span class="block text-sm font-medium text-gray-900 dark:text-gray-100">{{ t('feedback.notif.status') }}</span>
                  <span class="block text-xs text-gray-500 dark:text-gray-400">{{ t('feedback.notif.status_hint') }}</span>
                </span>
              </label>

              <label class="flex items-start gap-3 cursor-pointer opacity-60">
                <input
                  v-model="notifPrefs.email_weekly_summary"
                  type="checkbox"
                  disabled
                  class="mt-0.5 h-4 w-4 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
                  :aria-label="t('feedback.notif.weekly')"
                />
                <span class="flex-1">
                  <span class="block text-sm font-medium text-gray-900 dark:text-gray-100">{{ t('feedback.notif.weekly') }}</span>
                  <span class="block text-xs text-gray-500 dark:text-gray-400">{{ t('feedback.notif.weekly_hint') }}</span>
                </span>
              </label>
            </div>
            <div class="mt-6 flex justify-end">
              <button
                @click="saveNotifPrefs"
                :disabled="isSavingNotif || !notifLoaded"
                class="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded hover:bg-blue-700 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {{ isSavingNotif ? t('settings.saving') : t('settings.save') }}
              </button>
            </div>
          </div>

          <div v-if="activeTab === 'Avatar'">
            <h4 class="text-md font-medium text-gray-900 dark:text-white mb-4">{{ t('settings.profile_picture') }}</h4>

            <!-- Crop mode: zoom/reposition the chosen image, then save -->
            <AvatarCropper
              v-if="selectedFile"
              :file="selectedFile"
              @crop="onCropped"
              @cancel="resetSelection"
            />

            <!-- Otherwise: current avatar + choose-file entry point -->
            <div v-else class="flex items-center space-x-6">
              <div class="shrink-0">
                <img
                  v-if="user?.avatar_url"
                  :src="getFullUrl(user?.avatar_url ?? undefined)"
                  class="h-24 w-24 object-cover rounded-full border border-gray-200 dark:border-gray-700"
                  alt="Avatar"
                />
                <div v-else class="h-24 w-24 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center">
                  <span class="text-gray-500 dark:text-gray-300 text-2xl font-semibold">{{ userInitials(user) }}</span>
                </div>
              </div>
              <!-- Stacked vertically so both buttons stay visible on narrow
                   screens (side-by-side overflowed the panel on mobile). -->
              <div class="flex flex-col gap-2">
                <input
                  ref="fileInput"
                  type="file"
                  @change="handleFileChange"
                  accept="image/png, image/jpeg, image/gif, image/webp"
                  class="sr-only"
                  :aria-label="t('settings.choose_photo')"
                />
                <button
                  type="button"
                  @click="fileInput?.click()"
                  class="px-4 py-2 rounded-md text-sm font-semibold bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-900/30 dark:text-blue-300 dark:hover:bg-blue-900/50 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {{ t('settings.choose_file') }}
                </button>
                <button
                  v-if="user?.avatar_url"
                  type="button"
                  @click="removeAvatar"
                  :disabled="isRemoving"
                  class="px-4 py-2 rounded-md text-sm font-semibold bg-red-50 text-red-700 hover:bg-red-100 dark:bg-red-900/30 dark:text-red-300 dark:hover:bg-red-900/50 focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {{ t('settings.remove_photo') }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
