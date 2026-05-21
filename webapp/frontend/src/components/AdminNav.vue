<script setup lang="ts">
import { useI18n } from 'vue-i18n'
const { t } = useI18n({ useScope: 'global' })
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
      class="px-3 py-1.5 rounded-md text-sm font-medium inline-flex items-center gap-2"
      :class="$route.path.startsWith('/admin/upload') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >
      {{ t('admin.upload.tab') }}
      <span
        v-if="!$route.path.startsWith('/admin/upload')"
        class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
      >new</span>
    </router-link>
    <router-link
      to="/admin/comments"
      class="px-3 py-1.5 rounded-md text-sm font-medium"
      :class="$route.path.startsWith('/admin/comments') ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
    >{{ t('admin.comments.tab') }}</router-link>
  </div>
</template>
