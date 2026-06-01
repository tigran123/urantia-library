<script setup lang="ts">
import { ref, computed, onMounted, inject, type Ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { MegaphoneIcon, PaperAirplaneIcon } from '@heroicons/vue/24/outline'
import {
  createFeedback, uploadFeedbackAttachment, listAdmins,
  type AdminBrief, type FeedbackCategory, type FeedbackBookSubcategory,
} from '../api'
import FeedbackSubNav from '../components/feedback/FeedbackSubNav.vue'
import FeedbackCategoryPicker from '../components/feedback/FeedbackCategoryPicker.vue'
import FeedbackRecipientPicker from '../components/feedback/FeedbackRecipientPicker.vue'
import FeedbackScreenshotInput from '../components/feedback/FeedbackScreenshotInput.vue'
import BookContextChip from '../components/feedback/BookContextChip.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const currentUser = inject<Ref<{ id?: number; email: string; is_admin?: boolean } | null>>('currentUser')

const isAdmin = computed(() => !!currentUser?.value?.is_admin)

const category = ref<FeedbackCategory>(
  (route.query.cat as FeedbackCategory) || 'general',
)
const bookSub = ref<FeedbackBookSubcategory | null>(
  category.value === 'book' ? 'metadata' : null,
)
const subject = ref('')
const body = ref('')
const screenshot = ref<File | null>(null)
const recipientIds = ref<number[]>([])
const adminList = ref<AdminBrief[]>([])
const bookContext = ref<{ hash_id: string; title: string; author: string | null } | null>(null)
const submitting = ref(false)
const errorMsg = ref('')

const BODY_MAX = 4000
const SUBJECT_MAX = 200
const DIGEST_HOURS_HINT = 6

const canSend = computed(() =>
  subject.value.trim().length > 0
  && subject.value.length <= SUBJECT_MAX
  && body.value.trim().length > 0
  && body.value.length <= BODY_MAX
  && (category.value !== 'book' || !!bookSub.value)
  && !submitting.value,
)

function loadBookContext(hashId: string) {
  // Title/author are carried via query string from ItemView.vue. There's no
  // public single-book endpoint, and the chip is decoration — the backend
  // resolves title from book_hash_id for the inbox payload independently.
  const title = (route.query.book_title as string) || hashId
  const author = (route.query.book_author as string) || null
  bookContext.value = { hash_id: hashId, title, author }
}

onMounted(async () => {
  if (isAdmin.value) {
    try {
      const r = await listAdmins()
      adminList.value = r.data.items
    } catch {
      adminList.value = []
    }
  }
  if (typeof route.query.book === 'string' && route.query.book) {
    loadBookContext(route.query.book)
  }
})

// Re-prefill bookSub when admin flips to "book" category and none picked.
watch(category, (c) => {
  if (c === 'book' && !bookSub.value) bookSub.value = 'metadata'
  if (c !== 'book') bookSub.value = null
})

function captureDiag(): Record<string, string> {
  return {
    browser: navigator.userAgent.slice(0, 200),
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    route: route.fullPath.slice(0, 200),
    build: (import.meta.env.MODE || 'dev').slice(0, 50),
    locale: (navigator.language || 'en').slice(0, 20),
  }
}

async function submit() {
  if (!canSend.value) return
  submitting.value = true
  errorMsg.value = ''
  try {
    const payload = {
      category: category.value,
      book_subcategory: category.value === 'book' ? bookSub.value : null,
      subject: subject.value.trim(),
      body: body.value.trim(),
      book_hash_id: bookContext.value?.hash_id || null,
      diag: captureDiag(),
      ...(isAdmin.value ? { recipient_admin_ids: recipientIds.value } : {}),
    }
    const r = await createFeedback(payload)
    const { id, public_id } = r.data
    if (screenshot.value) {
      try {
        await uploadFeedbackAttachment(id, screenshot.value)
      } catch (e) {
        // Soft-fail the screenshot — the thread still exists.
        console.warn('Screenshot upload failed', e)
      }
    }
    router.push({ name: 'feedback-thread', params: { publicId: public_id } })
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.detail || 'Failed to send'
    submitting.value = false
  }
}

function onShortcut(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    submit()
  }
}
</script>

<template>
  <div class="px-4 sm:px-6 lg:px-8 py-8" @keydown="onShortcut">
    <FeedbackSubNav active="send" />

    <div class="grid grid-cols-12 gap-6">
      <div class="col-span-12 lg:col-span-8 space-y-6">
        <div v-if="bookContext" class="flex items-start gap-3 -mt-1">
          <BookContextChip
            :title="bookContext.title"
            :author="bookContext.author"
            removable
            @remove="bookContext = null"
          />
          <span class="text-xs text-gray-500 dark:text-gray-400 mt-1.5">
            {{ t('feedback.attached_book') }}
          </span>
        </div>

        <div v-if="isAdmin">
          <div class="text-xs font-semibold text-gray-700 dark:text-gray-200 uppercase tracking-wide mb-1.5">
            {{ t('feedback.to') }}
          </div>
          <FeedbackRecipientPicker
            :admins="adminList"
            v-model:selected-ids="recipientIds"
            :current-user-id="currentUser?.id"
          />
        </div>

        <div>
          <div class="text-xs font-semibold text-gray-700 dark:text-gray-200 uppercase tracking-wide mb-1.5">
            {{ t('feedback.what_about') }} <span class="text-red-500">*</span>
          </div>
          <FeedbackCategoryPicker
            v-model:category="category"
            v-model:book-subcategory="bookSub"
            :has-book="!!bookContext"
          />
        </div>

        <div>
          <div class="text-xs font-semibold text-gray-700 dark:text-gray-200 uppercase tracking-wide mb-1.5">
            {{ t('feedback.subject') }} <span class="text-red-500">*</span>
          </div>
          <input
            v-model="subject"
            type="text"
            :placeholder="t('feedback.subject_placeholder')"
            :maxlength="SUBJECT_MAX"
            class="block w-full border border-gray-300 dark:border-gray-600 rounded-md py-2 px-3 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
          />
        </div>

        <div>
          <div class="flex items-center justify-between mb-1.5">
            <span class="text-xs font-semibold text-gray-700 dark:text-gray-200 uppercase tracking-wide">
              {{ t('feedback.message') }} <span class="text-red-500">*</span>
            </span>
            <span class="text-xs text-gray-400 dark:text-gray-500">
              {{ body.length }} / {{ BODY_MAX }}
            </span>
          </div>
          <textarea
            v-model="body"
            rows="8"
            :placeholder="t('feedback.body_placeholder')"
            :maxlength="BODY_MAX"
            class="block w-full border border-gray-300 dark:border-gray-600 rounded-md py-2 px-3 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 resize-y leading-relaxed"
          ></textarea>
        </div>

        <div>
          <div class="text-xs font-semibold text-gray-700 dark:text-gray-200 uppercase tracking-wide mb-1.5">
            {{ t('feedback.attach_screenshot') }}
          </div>
          <FeedbackScreenshotInput v-model="screenshot" />
          <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ t('feedback.attach_hint') }}</div>
        </div>

        <div v-if="errorMsg" class="rounded-md border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-900/30 px-3 py-2 text-sm text-red-700 dark:text-red-300">
          {{ errorMsg }}
        </div>

        <div class="flex items-center justify-between pt-2 border-t border-gray-100 dark:border-gray-700">
          <div class="text-xs text-gray-500 dark:text-gray-400">
            <kbd class="px-1.5 py-0.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 font-mono text-[10px]">Ctrl ⏎</kbd>
            to send
          </div>
          <div class="flex items-center gap-3">
            <button
              type="button"
              class="text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white px-3 py-2"
              @click="router.back()"
            >
              {{ t('feedback.cancel') }}
            </button>
            <button
              type="button"
              :disabled="!canSend"
              :class="[
                'inline-flex items-center gap-2 text-white text-sm font-medium px-4 py-2 rounded',
                canSend ? 'bg-blue-600 hover:bg-blue-700' : 'bg-blue-300 dark:bg-blue-800 cursor-not-allowed',
              ]"
              @click="submit"
            >
              <PaperAirplaneIcon class="h-4 w-4" />
              {{ t('feedback.send_button') }}
            </button>
          </div>
        </div>
      </div>

      <aside class="col-span-12 lg:col-span-4 space-y-4">
        <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
          <div class="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-gray-100">
            <MegaphoneIcon class="h-4 w-4 text-blue-600 dark:text-blue-400" />
            {{ t('feedback.sidebar_title') }}
          </div>
          <p class="text-sm text-gray-600 dark:text-gray-400 mt-2 leading-relaxed">
            {{ t('feedback.sidebar_body') }}
          </p>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-2">
            {{ t('feedback.sidebar_digest_hint', { h: DIGEST_HOURS_HINT }) }}
          </p>
        </div>

        <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
          <div class="font-semibold text-gray-700 dark:text-gray-200 text-sm mb-1">{{ t('feedback.tip_title') }}</div>
          <ul class="list-disc pl-4 space-y-1">
            <li>{{ t('feedback.tip_refresh') }}</li>
            <li>{{ t('feedback.tip_book_report') }}</li>
            <li>{{ t('feedback.tip_screenshot') }}</li>
          </ul>
        </div>
      </aside>
    </div>
  </div>
</template>
