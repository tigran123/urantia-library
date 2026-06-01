<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChatBubbleLeftRightIcon } from '@heroicons/vue/24/outline'
import {
  listMyFeedback,
  type FeedbackThreadSummary, type FeedbackStatus,
} from '../api'
import FeedbackSubNav from '../components/feedback/FeedbackSubNav.vue'
import FeedbackStatusPill from '../components/feedback/FeedbackStatusPill.vue'

const { t } = useI18n()
const items = ref<FeedbackThreadSummary[]>([])
const filter = ref<FeedbackStatus | 'all'>('all')
const loading = ref(false)

const FILTERS: (FeedbackStatus | 'all')[] = [
  'all', 'open', 'progress', 'waiting', 'resolved', 'closed',
]

async function load() {
  loading.value = true
  try {
    const r = await listMyFeedback(filter.value)
    items.value = r.data.items
  } finally {
    loading.value = false
  }
}

function relTime(iso: string) {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function categoryLabel(item: FeedbackThreadSummary) {
  if (item.category === 'book' && item.book_subcategory) {
    return `${t('feedback.categories.book')} · ${t(`feedback.book_subs.${item.book_subcategory}`)}`
  }
  return t(`feedback.categories.${item.category}`)
}

onMounted(load)
const empty = computed(() => !loading.value && items.value.length === 0)
</script>

<template>
  <div class="px-4 sm:px-6 lg:px-8 py-8">
    <FeedbackSubNav active="mine" />

    <div class="flex flex-wrap items-center gap-1.5 mb-4">
      <button
        v-for="f in FILTERS"
        :key="f"
        type="button"
        :class="[
          'px-2.5 py-1 rounded-full text-xs font-medium border transition',
          filter === f
            ? 'bg-blue-600 text-white border-blue-600'
            : 'bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600',
        ]"
        @click="filter = f; load()"
      >
        {{ f === 'all' ? t('feedback.filter.all') : t(`feedback.status.${f}`) }}
      </button>
    </div>

    <div v-if="loading" class="text-sm text-gray-500 dark:text-gray-400">Loading…</div>
    <div
      v-else-if="empty"
      class="text-center py-16 text-sm text-gray-500 dark:text-gray-400 border border-dashed border-gray-300 dark:border-gray-700 rounded-lg"
    >
      <ChatBubbleLeftRightIcon class="h-10 w-10 mx-auto text-gray-300 dark:text-gray-600 mb-2" />
      {{ t('feedback.empty_list') }}
    </div>

    <ul v-else class="divide-y divide-gray-200 dark:divide-gray-700 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      <li v-for="t_ in items" :key="t_.id">
        <router-link
          :to="{ name: 'feedback-thread', params: { publicId: t_.public_id } }"
          class="flex items-center gap-4 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/40 transition-colors"
        >
          <span class="text-[11px] font-mono text-gray-500 dark:text-gray-400 w-28 shrink-0 truncate">
            {{ t_.public_id }}
          </span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                {{ t_.subject }}
              </span>
              <span
                v-if="t_.has_unread"
                class="inline-block h-1.5 w-1.5 rounded-full bg-blue-500"
                :title="'Unread update'"
              ></span>
            </div>
            <div class="text-xs text-gray-500 dark:text-gray-400 truncate">
              {{ categoryLabel(t_) }}
              <template v-if="t_.book_title"> · {{ t_.book_title }}</template>
            </div>
          </div>
          <FeedbackStatusPill :status="t_.status" />
          <span class="text-xs text-gray-400 dark:text-gray-500 hidden md:inline w-32 text-right">
            {{ relTime(t_.updated_at) }}
          </span>
        </router-link>
      </li>
    </ul>
  </div>
</template>
