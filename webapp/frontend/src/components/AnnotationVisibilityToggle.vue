<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { EyeIcon, EyeSlashIcon, UserIcon } from '@heroicons/vue/24/outline'
import type { AnnotationVisibility } from '../composables/useAnnotations'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{ modelValue: AnnotationVisibility }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: AnnotationVisibility): void }>()

const next = (): AnnotationVisibility => {
  if (props.modelValue === 'all') return 'mine'
  if (props.modelValue === 'mine') return 'none'
  return 'all'
}

const onClick = () => emit('update:modelValue', next())

const label = () => {
  if (props.modelValue === 'all') return t('app.annotation_visibility_all')
  if (props.modelValue === 'mine') return t('app.annotation_visibility_mine')
  return t('app.annotation_visibility_none')
}
</script>

<template>
  <button
    @click="onClick"
    :title="`${t('app.annotation_visibility')}: ${label()}`"
    :class="[
      'px-2 py-1 rounded text-sm flex items-center gap-1 transition-colors',
      modelValue === 'none'
        ? 'bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-500 dark:text-gray-400'
        : 'bg-blue-100 hover:bg-blue-200 dark:bg-blue-900/40 dark:hover:bg-blue-900/60 text-blue-700 dark:text-blue-200',
    ]"
  >
    <EyeIcon v-if="modelValue === 'all'" class="w-5 h-5" />
    <UserIcon v-else-if="modelValue === 'mine'" class="w-5 h-5" />
    <EyeSlashIcon v-else class="w-5 h-5" />
  </button>
</template>
