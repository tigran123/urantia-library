<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ClearancePill from './ClearancePill.vue'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{
  modelValue: number
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: number): void
}>()

const onInput = (e: Event) => {
  const raw = Number((e.target as HTMLInputElement).value)
  if (Number.isNaN(raw)) return
  const clamped = Math.max(0, Math.min(100, Math.floor(raw)))
  emit('update:modelValue', clamped)
}
</script>

<template>
  <div>
    <div class="flex items-center gap-3">
      <input
        :value="modelValue"
        @input="onInput"
        type="number"
        min="0"
        max="100"
        class="w-20 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
      />
      <ClearancePill :value="modelValue" />
    </div>
    <p class="mt-2 text-xs text-gray-500 dark:text-gray-400">
      {{ t('admin.upload.review.clearance_help', { zero: '0', hundred: '100' }) }}
    </p>
  </div>
</template>
