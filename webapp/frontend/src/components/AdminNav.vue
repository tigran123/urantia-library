<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { feedbackActiveCount, refreshFeedbackActiveCount } from '../feedbackBadge'

const { t } = useI18n({ useScope: 'global' })
const route = useRoute()

onMounted(refreshFeedbackActiveCount)
// Also re-poll on navigation between admin pages — covers the case where
// the count changed in a tab we'd already mounted AdminNav in.
watch(() => route.path, refreshFeedbackActiveCount)
</script>

<template>
  <div class="flex items-center gap-2 border-b border-gray-200 dark:border-gray-700 pb-2 mb-2">
    <h1 class="text-2xl font-bold text-gray-900 dark:text-white mr-6">{{ t('admin.title') }}</h1>
    <router-link
      to="/admin/users"
      class="px-3 py-1.5 rounded-md text-sm font-medium"
      :class="$route.path.startsWith('/admin/users') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >{{ t('admin.users') }}</router-link>
    <router-link
      to="/admin/books"
      class="px-3 py-1.5 rounded-md text-sm font-medium"
      :class="$route.path.startsWith('/admin/books') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >{{ t('admin.books') }}</router-link>
    <router-link
      to="/admin/integrity"
      class="px-3 py-1.5 rounded-md text-sm font-medium"
      :class="$route.path.startsWith('/admin/integrity') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >{{ t('admin.integrity.tab') }}</router-link>
    <router-link
      to="/admin/upload"
      class="px-3 py-1.5 rounded-md text-sm font-medium"
      :class="$route.path.startsWith('/admin/upload') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >{{ t('admin.upload.tab') }}</router-link>
    <router-link
      to="/admin/comments"
      class="px-3 py-1.5 rounded-md text-sm font-medium"
      :class="$route.path.startsWith('/admin/comments') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >{{ t('admin.comments.tab') }}</router-link>
    <router-link
      to="/admin/feedback"
      class="px-3 py-1.5 rounded-md text-sm font-medium inline-flex items-center gap-1.5"
      :class="$route.path.startsWith('/admin/feedback') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >
      {{ t('admin.feedback.tab') }}
      <span
        v-if="feedbackActiveCount > 0"
        class="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold"
        :class="$route.path.startsWith('/admin/feedback') ? 'bg-white text-blue-700' : 'bg-blue-600 text-white'"
      >
        {{ feedbackActiveCount }}
      </span>
    </router-link>
    <router-link
      to="/admin/audit"
      class="px-3 py-1.5 rounded-md text-sm font-medium"
      :class="$route.path.startsWith('/admin/audit') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >{{ t('admin.audit.tab') }}</router-link>
    <router-link
      to="/admin/usage"
      class="px-3 py-1.5 rounded-md text-sm font-medium"
      :class="$route.path.startsWith('/admin/usage') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >{{ t('admin.usage.tab') }}</router-link>
  </div>
</template>
