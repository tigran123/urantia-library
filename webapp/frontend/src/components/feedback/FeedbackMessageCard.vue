<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ShieldCheckIcon, ChatBubbleLeftEllipsisIcon, LockClosedIcon } from '@heroicons/vue/24/outline'
import type { FeedbackMessageNode, FeedbackStatus } from '../../api'
import FeedbackStatusPill from './FeedbackStatusPill.vue'

const props = defineProps<{
  message: FeedbackMessageNode
  previousStatus?: FeedbackStatus | null
}>()
const { t } = useI18n()

const kindCls = computed(() => {
  switch (props.message.kind) {
    case 'admin':
      return 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800'
    case 'internal':
      return 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800'
    case 'status':
      return 'bg-gray-50 dark:bg-gray-800/40 border-gray-200 dark:border-gray-700'
    default:
      return 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700'
  }
})

function formatDate(iso: string) {
  try {
    const d = new Date(iso)
    return d.toLocaleString()
  } catch {
    return iso
  }
}
</script>

<template>
  <div
    v-if="message.kind === 'status'"
    class="flex flex-wrap items-center gap-2 text-xs text-gray-500 dark:text-gray-400 py-2"
  >
    <span class="font-medium text-gray-700 dark:text-gray-300">{{ message.author_name }}</span>
    <span>· status:</span>
    <FeedbackStatusPill v-if="previousStatus" :status="previousStatus" />
    <span aria-hidden="true">→</span>
    <FeedbackStatusPill :status="message.body as FeedbackStatus" />
    <span class="ml-auto">{{ formatDate(message.created_at) }}</span>
  </div>
  <div
    v-else
    :class="['rounded-lg border p-4', kindCls]"
  >
    <div class="flex items-center gap-2 text-xs">
      <span class="font-semibold text-gray-900 dark:text-gray-100">
        {{ message.author_name }}{{ message.is_own ? ' · ' + t('feedback.you') : '' }}
      </span>
      <span
        v-if="message.is_admin"
        class="inline-flex items-center gap-0.5 text-blue-700 dark:text-blue-300"
      >
        <ShieldCheckIcon class="h-3 w-3" />
        admin
      </span>
      <span
        v-if="message.kind === 'internal'"
        class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-200 text-[10px] font-medium"
      >
        <LockClosedIcon class="h-3 w-3" />
        {{ t('admin.feedback.internal_note') }}
      </span>
      <span
        v-else-if="message.kind === 'admin'"
        class="inline-flex items-center gap-0.5 text-blue-700 dark:text-blue-300"
      >
        <ChatBubbleLeftEllipsisIcon class="h-3 w-3" />
      </span>
      <span class="ml-auto text-gray-500 dark:text-gray-400">{{ formatDate(message.created_at) }}</span>
    </div>
    <div class="mt-2 whitespace-pre-wrap text-sm text-gray-800 dark:text-gray-200 leading-relaxed">
      {{ message.body }}
    </div>
  </div>
</template>
