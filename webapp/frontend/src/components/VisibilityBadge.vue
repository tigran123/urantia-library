<script setup lang="ts">
// Private (B&W/gray) vs Public (blue) pill — matches the convention in
// AnnotationVisibilityToggle.vue. The two states must never look identical.
import { useI18n } from 'vue-i18n'
import { LockClosedIcon, GlobeAltIcon } from '@heroicons/vue/24/outline'

defineProps<{ visibility: 'private' | 'public' }>()
const { t } = useI18n({ useScope: 'global' })
</script>

<template>
  <span
    :class="[
      'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium',
      visibility === 'public'
        ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200'
        : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
    ]"
  >
    <GlobeAltIcon v-if="visibility === 'public'" class="h-3.5 w-3.5" />
    <LockClosedIcon v-else class="h-3.5 w-3.5" />
    {{ visibility === 'public' ? t('playlists.public') : t('playlists.private') }}
  </span>
</template>
