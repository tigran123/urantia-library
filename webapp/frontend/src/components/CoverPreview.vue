<script setup lang="ts">
import { computed, ref, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { PhotoIcon, ArrowPathIcon } from '@heroicons/vue/24/outline'

const { t } = useI18n({ useScope: 'global' })

const props = withDefaults(defineProps<{
  imageUrl?: string | null
  file?: File | null
  size?: 'full' | 'small'
  readonly?: boolean
  meta?: string
}>(), {
  imageUrl: null,
  file: null,
  size: 'full',
  readonly: false,
  meta: '',
})

const emit = defineEmits<{
  (e: 'update:file', value: File | null): void
  (e: 're-extract'): void
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const localUrl = ref<string | null>(null)

const refreshLocalUrl = (f: File | null) => {
  if (localUrl.value) {
    URL.revokeObjectURL(localUrl.value)
    localUrl.value = null
  }
  if (f) {
    localUrl.value = URL.createObjectURL(f)
  }
}

watch(() => props.file, (f) => refreshLocalUrl(f), { immediate: true })

onUnmounted(() => {
  if (localUrl.value) URL.revokeObjectURL(localUrl.value)
})

const displayUrl = computed(() => localUrl.value || props.imageUrl || null)

const cardSizeClasses = computed(() =>
  props.size === 'small'
    ? 'w-24 h-32'
    : 'w-full aspect-[3/4]',
)

const openPicker = () => fileInput.value?.click()

const onFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    emit('update:file', input.files[0])
  }
  input.value = ''
}
</script>

<template>
  <div>
    <div
      class="rounded-md overflow-hidden shadow-sm border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 flex items-center justify-center"
      :class="cardSizeClasses"
    >
      <img
        v-if="displayUrl"
        :src="displayUrl"
        alt="Cover"
        class="w-full h-full object-contain"
      />
      <div
        v-else
        class="stripe-placeholder w-full h-full flex items-center justify-center text-gray-400 dark:text-gray-500"
      >
        <PhotoIcon class="w-8 h-8 opacity-60" />
      </div>
    </div>
    <p v-if="meta" class="mt-1.5 text-[11px] font-mono text-gray-500 dark:text-gray-400">{{ meta }}</p>
    <div v-if="!readonly" class="mt-3 space-y-2">
      <button
        type="button"
        @click="openPicker"
        class="w-full inline-flex items-center justify-center gap-2 px-3 py-1.5 text-xs font-medium rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
      >
        <PhotoIcon class="w-4 h-4" />
        {{ t('admin.upload.review.replace_cover') }}
      </button>
      <button
        type="button"
        @click="emit('re-extract')"
        class="w-full inline-flex items-center justify-center gap-2 px-3 py-1.5 text-xs font-medium rounded text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
      >
        <ArrowPathIcon class="w-4 h-4" />
        {{ t('admin.upload.review.reextract') }}
      </button>
    </div>
    <input
      ref="fileInput"
      type="file"
      accept="image/png,image/jpeg,image/webp"
      class="hidden"
      @change="onFileChange"
    />
  </div>
</template>

<style scoped>
.stripe-placeholder {
  background-image:
    repeating-linear-gradient(135deg,
      rgba(0,0,0,0.04) 0 10px, transparent 10px 20px);
}
:global(.dark) .stripe-placeholder {
  background-image:
    repeating-linear-gradient(135deg,
      rgba(255,255,255,0.05) 0 10px, transparent 10px 20px);
}
</style>
