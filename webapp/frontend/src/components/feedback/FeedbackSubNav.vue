<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { MegaphoneIcon, InboxIcon } from '@heroicons/vue/24/outline'

defineProps<{ active: 'send' | 'mine' }>()

const { t } = useI18n()
</script>

<template>
  <div class="flex items-center gap-2 border-b border-gray-200 dark:border-gray-700 pb-3 mb-6">
    <h1 class="text-2xl font-bold text-gray-900 dark:text-white mr-6">{{ t('feedback.title') }}</h1>
    <router-link
      :to="{ name: 'feedback-compose' }"
      class="px-3 py-1.5 rounded-md text-sm font-medium inline-flex items-center gap-1.5"
      :class="active === 'send'
        ? 'bg-blue-600 text-white'
        : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >
      <MegaphoneIcon class="h-4 w-4" />
      {{ t('feedback.send') }}
    </router-link>
    <router-link
      :to="{ name: 'feedback-mine' }"
      class="px-3 py-1.5 rounded-md text-sm font-medium inline-flex items-center gap-1.5"
      :class="active === 'mine'
        ? 'bg-blue-600 text-white'
        : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >
      <InboxIcon class="h-4 w-4" />
      {{ t('feedback.my') }}
    </router-link>
  </div>
</template>
