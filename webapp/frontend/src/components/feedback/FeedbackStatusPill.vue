<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { FeedbackStatus } from '../../api'

const props = defineProps<{
  status: FeedbackStatus
  size?: 'sm' | 'md'
}>()

const { t } = useI18n()

const palette: Record<FeedbackStatus, string> = {
  new: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200',
  open: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200',
  triage: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
  progress: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-200',
  waiting: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200',
  resolved: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200',
  closed: 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
  archived: 'bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
}

const sizeCls = computed(() =>
  props.size === 'md'
    ? 'px-2.5 py-1 text-xs'
    : 'px-2 py-0.5 text-[11px]',
)
const cls = computed(() => palette[props.status] || palette.new)
const label = computed(() => t(`feedback.status.${props.status}`))
</script>

<template>
  <span :class="['inline-flex items-center rounded-full font-medium', sizeCls, cls]">
    {{ label }}
  </span>
</template>
