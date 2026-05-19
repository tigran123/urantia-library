<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { LockClosedIcon } from '@heroicons/vue/24/outline'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{
  value: number
}>()

type Tier = { label: string; classes: string }

const tier = computed<Tier>(() => {
  const v = props.value
  if (v >= 100) {
    return {
      label: t('admin.upload.clearance_label.admin_only'),
      classes: 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800',
    }
  }
  if (v >= 50) {
    return {
      label: t('admin.upload.clearance_label.restricted'),
      classes: 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-800',
    }
  }
  if (v > 0) {
    return {
      label: t('admin.upload.clearance_label.members'),
      classes: 'bg-sky-50 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300 border-sky-200 dark:border-sky-800',
    }
  }
  return {
    label: t('admin.upload.clearance_label.public'),
    classes: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800',
  }
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border"
    :class="tier.classes"
  >
    <LockClosedIcon class="w-3 h-3" />
    {{ tier.label }}
  </span>
</template>
