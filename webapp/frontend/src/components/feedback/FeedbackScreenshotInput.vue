<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { PhotoIcon, TrashIcon } from '@heroicons/vue/24/outline'

const props = defineProps<{
  modelValue: File | null
  maxBytes?: number
}>()
const emit = defineEmits<{ (e: 'update:modelValue', f: File | null): void }>()

const { t } = useI18n()
const ALLOWED = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
const MAX = props.maxBytes ?? 5 * 1024 * 1024
const error = ref('')
const dragging = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)
const previewUrl = ref<string | null>(null)

function setFile(f: File | null) {
  error.value = ''
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = null
  }
  if (!f) {
    emit('update:modelValue', null)
    return
  }
  if (!ALLOWED.includes(f.type)) {
    error.value = 'Unsupported image type — PNG, JPG, GIF, WEBP only.'
    return
  }
  if (f.size > MAX) {
    error.value = `File too large (max ${(MAX / 1024 / 1024).toFixed(0)} MB).`
    return
  }
  previewUrl.value = URL.createObjectURL(f)
  emit('update:modelValue', f)
}

function onPick(e: Event) {
  const t = e.target as HTMLInputElement
  setFile(t.files?.[0] ?? null)
  t.value = ''
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragging.value = false
  setFile(e.dataTransfer?.files?.[0] ?? null)
}

function fmtSize(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div>
    <div
      v-if="modelValue"
      class="flex items-center gap-3 p-3 border border-gray-200 dark:border-gray-700 rounded-md bg-gray-50 dark:bg-gray-800/50"
    >
      <img
        v-if="previewUrl"
        :src="previewUrl"
        alt=""
        class="w-16 h-12 rounded border border-gray-200 dark:border-gray-700 object-cover"
      />
      <div class="flex-1 min-w-0">
        <div class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
          {{ modelValue.name }}
        </div>
        <div class="text-xs text-gray-500 dark:text-gray-400">
          {{ fmtSize(modelValue.size) }}
        </div>
      </div>
      <button
        type="button"
        class="text-gray-400 hover:text-red-500 dark:hover:text-red-400"
        :aria-label="t('feedback.cancel')"
        @click="setFile(null)"
      >
        <TrashIcon class="h-4 w-4" />
      </button>
    </div>
    <button
      v-else
      type="button"
      :class="[
        'w-full border-2 border-dashed rounded-md px-4 py-5 text-center text-sm transition',
        dragging
          ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'
          : 'border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:border-gray-400 dark:hover:border-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800/50',
      ]"
      @click="inputRef?.click()"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop="onDrop"
    >
      <PhotoIcon class="h-6 w-6 mx-auto text-gray-400 mb-1.5" />
      {{ t('feedback.drop_screenshot') }}
      <span class="text-blue-600 dark:text-blue-400 font-medium">{{ t('feedback.browse') }}</span>
      <div class="text-xs text-gray-400 dark:text-gray-500 mt-1">
        {{ t('feedback.file_limits') }}
      </div>
    </button>
    <input
      ref="inputRef"
      type="file"
      accept="image/png,image/jpeg,image/gif,image/webp"
      class="hidden"
      @change="onPick"
    />
    <div v-if="error" class="mt-1.5 text-xs text-red-600 dark:text-red-400">
      {{ error }}
    </div>
  </div>
</template>
