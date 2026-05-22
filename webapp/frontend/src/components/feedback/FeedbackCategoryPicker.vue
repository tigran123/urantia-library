<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import {
  ChatBubbleLeftRightIcon, BugAntIcon, SparklesIcon, FlagIcon,
  BookOpenIcon, DocumentTextIcon,
} from '@heroicons/vue/24/outline'
import type { FeedbackCategory, FeedbackBookSubcategory } from '../../api'

const props = defineProps<{
  category: FeedbackCategory
  bookSubcategory: FeedbackBookSubcategory | null
  hasBook: boolean
}>()
const emit = defineEmits<{
  (e: 'update:category', c: FeedbackCategory): void
  (e: 'update:bookSubcategory', s: FeedbackBookSubcategory | null): void
}>()

const { t } = useI18n()

const categories: { id: FeedbackCategory; icon: any }[] = [
  { id: 'general', icon: ChatBubbleLeftRightIcon },
  { id: 'bug', icon: BugAntIcon },
  { id: 'feature', icon: SparklesIcon },
  { id: 'book', icon: FlagIcon },
  { id: 'acquire', icon: BookOpenIcon },
  { id: 'other', icon: DocumentTextIcon },
]

const subs: FeedbackBookSubcategory[] = [
  'metadata', 'corrupt', 'copyright', 'inappropriate', 'duplicate',
]

function pickCat(c: FeedbackCategory) {
  emit('update:category', c)
  if (c !== 'book') emit('update:bookSubcategory', null)
  else if (!props.bookSubcategory) emit('update:bookSubcategory', 'metadata')
}

function pickSub(s: FeedbackBookSubcategory) {
  emit('update:bookSubcategory', s)
}
</script>

<template>
  <div>
    <div role="radiogroup" :aria-label="t('feedback.what_about')" class="grid grid-cols-2 md:grid-cols-3 gap-2">
      <button
        v-for="c in categories"
        :key="c.id"
        type="button"
        role="radio"
        :aria-checked="c.id === category"
        :class="[
          'group text-left p-3 rounded-md border transition',
          c.id === category
            ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 ring-1 ring-blue-500'
            : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700/50',
        ]"
        @click="pickCat(c.id)"
      >
        <div class="flex items-center gap-2">
          <component
            :is="c.icon"
            :class="['h-4 w-4 shrink-0', c.id === category ? 'text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400']"
          />
          <span :class="['text-sm font-medium', c.id === category ? 'text-blue-900 dark:text-blue-200' : 'text-gray-900 dark:text-gray-100']">
            {{ t(`feedback.categories.${c.id}`) }}
          </span>
        </div>
        <div :class="['text-xs mt-0.5', c.id === category ? 'text-blue-800/80 dark:text-blue-300/80' : 'text-gray-500 dark:text-gray-400']">
          {{ t(`feedback.category_hints.${c.id}`) }}
        </div>
      </button>
    </div>

    <div
      v-if="category === 'book'"
      class="mt-3 pl-3 border-l-2 border-amber-300 dark:border-amber-700"
    >
      <div class="text-xs font-medium text-gray-600 dark:text-gray-300 mb-1.5">
        {{ t('feedback.what_kind') }}
      </div>
      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="s in subs"
          :key="s"
          type="button"
          :class="[
            'px-2.5 py-1 rounded-full border text-xs font-medium transition',
            s === bookSubcategory
              ? 'bg-amber-100 dark:bg-amber-900/40 border-amber-400 dark:border-amber-700 text-amber-900 dark:text-amber-200'
              : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:border-gray-300 dark:hover:border-gray-600',
          ]"
          @click="pickSub(s)"
        >
          {{ t(`feedback.book_subs.${s}`) }}
        </button>
      </div>
      <div
        v-if="!hasBook"
        class="mt-2 text-xs text-amber-700 dark:text-amber-400 inline-flex items-center gap-1"
      >
        <FlagIcon class="h-3.5 w-3.5" />
        {{ t('feedback.book_problem_hint') }}
      </div>
    </div>
  </div>
</template>
