<script setup lang="ts">
// Create or edit a playlist. Centered modal with backdrop (same shape as the
// app's other modals). The Private/Public picker selected states are gray vs
// blue — never identical. Performs the API call itself and emits the result;
// Delete is offered only when editing a non-Bookshelf playlist.
import { ref, computed, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { XMarkIcon, LockClosedIcon, GlobeAltIcon, TrashIcon } from '@heroicons/vue/24/outline'
import { createPlaylist, updatePlaylist, deletePlaylist, type PlaylistSummary, type PlaylistVisibility } from '../api'

const props = defineProps<{ playlist?: PlaylistSummary | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', playlist: PlaylistSummary): void
  (e: 'deleted', id: number): void
}>()

const { t } = useI18n({ useScope: 'global' })

const isEdit = computed(() => !!props.playlist)
const canDelete = computed(() => !!props.playlist && !props.playlist.is_bookshelf)

const name = ref(props.playlist?.name ?? '')
const description = ref(props.playlist?.description ?? '')
const visibility = ref<PlaylistVisibility>(props.playlist?.visibility ?? 'private')
const submitting = ref(false)
const error = ref('')
const nameInput = ref<HTMLInputElement | null>(null)

onMounted(async () => {
  await nextTick()
  nameInput.value?.focus()
})

const submit = async () => {
  if (!name.value.trim() || submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const payload = {
      name: name.value.trim(),
      description: description.value.trim() || null,
      visibility: visibility.value,
    }
    const res = props.playlist
      ? await updatePlaylist(props.playlist.id, payload)
      : await createPlaylist(payload)
    emit('saved', res.data)
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    submitting.value = false
  }
}

const onDelete = async () => {
  if (!props.playlist || !canDelete.value) return
  if (!window.confirm(t('playlists.delete_confirm'))) return
  submitting.value = true
  try {
    await deletePlaylist(props.playlist.id)
    emit('deleted', props.playlist.id)
  } catch (e: any) {
    error.value = e.response?.data?.detail || e.message
    submitting.value = false
  }
}
</script>

<template>
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
    role="dialog"
    aria-modal="true"
    @mousedown.self="emit('close')"
  >
    <div class="max-w-md w-full bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-gray-200 dark:border-gray-700">
      <div class="flex items-center justify-between px-6 pt-5 pb-3">
        <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">
          {{ isEdit ? t('playlists.edit_title') : t('playlists.create_title') }}
        </h2>
        <button @click="emit('close')" class="p-1 rounded-md text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700">
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <div class="px-6 pb-2 space-y-4">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('playlists.name_label') }}</label>
          <input
            ref="nameInput"
            v-model="name"
            type="text"
            :placeholder="t('playlists.name_placeholder')"
            class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            @keydown.enter="submit"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('playlists.desc_label') }}</label>
          <textarea
            v-model="description"
            rows="3"
            :placeholder="t('playlists.desc_placeholder')"
            class="w-full px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
          ></textarea>
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('playlists.visibility_label') }}</label>
          <div class="grid grid-cols-2 gap-3">
            <button
              type="button"
              @click="visibility = 'private'"
              :class="[
                'text-left p-3 rounded-lg border transition-colors',
                visibility === 'private'
                  ? 'border-gray-400 bg-gray-100 dark:bg-gray-700 ring-1 ring-gray-400'
                  : 'border-gray-200 dark:border-gray-700 hover:border-gray-300',
              ]"
            >
              <div class="flex items-center gap-1.5 font-medium text-gray-900 dark:text-gray-100">
                <LockClosedIcon class="h-4 w-4" /> {{ t('playlists.vis_private') }}
              </div>
              <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ t('playlists.vis_private_desc') }}</p>
            </button>
            <button
              type="button"
              @click="visibility = 'public'"
              :class="[
                'text-left p-3 rounded-lg border transition-colors',
                visibility === 'public'
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30 ring-1 ring-blue-500'
                  : 'border-gray-200 dark:border-gray-700 hover:border-gray-300',
              ]"
            >
              <div class="flex items-center gap-1.5 font-medium" :class="visibility === 'public' ? 'text-blue-700 dark:text-blue-300' : 'text-gray-900 dark:text-gray-100'">
                <GlobeAltIcon class="h-4 w-4" /> {{ t('playlists.vis_public') }}
              </div>
              <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ t('playlists.vis_public_desc') }}</p>
            </button>
          </div>
        </div>

        <p v-if="error" class="text-sm text-red-600 dark:text-red-400">{{ error }}</p>
      </div>

      <div class="flex items-center justify-between px-6 py-4 mt-2 border-t border-gray-100 dark:border-gray-700">
        <button
          v-if="canDelete"
          @click="onDelete"
          class="inline-flex items-center gap-1 text-sm font-medium text-red-600 dark:text-red-400 hover:text-red-700"
        >
          <TrashIcon class="h-4 w-4" /> {{ t('playlists.delete') }}
        </button>
        <span v-else></span>
        <div class="flex items-center gap-2">
          <button @click="emit('close')" class="px-4 py-2 rounded-md text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-600">
            {{ t('playlists.cancel') }}
          </button>
          <button
            @click="submit"
            :disabled="!name.trim() || submitting"
            class="px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {{ isEdit ? t('playlists.save') : t('playlists.create') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
