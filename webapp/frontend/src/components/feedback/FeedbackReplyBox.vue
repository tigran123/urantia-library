<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { PaperAirplaneIcon } from '@heroicons/vue/24/outline'
import type { FeedbackStatus } from '../../api'

const props = defineProps<{
  asAdmin?: boolean
  currentStatus?: FeedbackStatus
  disabled?: boolean
}>()
const emit = defineEmits<{
  (e: 'send', payload: { body: string; internal: boolean; new_status: FeedbackStatus | null }): void
}>()

const { t } = useI18n()
const body = ref('')
const internal = ref(false)
const markResolved = ref(false)
const newStatus = ref<FeedbackStatus | ''>('')
const sending = ref(false)

const BODY_MAX = 4000
const canSend = computed(() =>
  body.value.trim().length > 0 && body.value.length <= BODY_MAX && !sending.value && !props.disabled,
)

async function onSend() {
  if (!canSend.value) return
  sending.value = true
  let ns: FeedbackStatus | null = null
  if (props.asAdmin) {
    if (markResolved.value) ns = 'resolved'
    else if (newStatus.value) ns = newStatus.value as FeedbackStatus
  }
  emit('send', { body: body.value.trim(), internal: internal.value, new_status: ns })
  body.value = ''
  internal.value = false
  markResolved.value = false
  newStatus.value = ''
  sending.value = false
}

const STATUSES: FeedbackStatus[] = [
  'open', 'triage', 'progress', 'waiting', 'resolved', 'closed',
]
</script>

<template>
  <div class="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
    <textarea
      v-model="body"
      rows="4"
      :placeholder="t('feedback.thread_reply_placeholder')"
      :maxlength="BODY_MAX"
      class="block w-full border border-gray-300 dark:border-gray-600 rounded-md py-2 px-3 text-sm bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 resize-y leading-relaxed"
    ></textarea>
    <div class="flex flex-wrap items-center gap-3 mt-3">
      <template v-if="asAdmin">
        <label class="inline-flex items-center gap-1.5 text-xs text-gray-700 dark:text-gray-300 cursor-pointer">
          <input
            v-model="internal"
            type="checkbox"
            class="h-3.5 w-3.5 rounded border-gray-300 dark:border-gray-600 text-amber-500 focus:ring-amber-500"
          />
          {{ t('admin.feedback.internal_note') }}
        </label>
        <label class="inline-flex items-center gap-1.5 text-xs text-gray-700 dark:text-gray-300 cursor-pointer">
          <input
            v-model="markResolved"
            type="checkbox"
            class="h-3.5 w-3.5 rounded border-gray-300 dark:border-gray-600 text-emerald-500 focus:ring-emerald-500"
          />
          {{ t('admin.feedback.mark_resolved_on_send') }}
        </label>
        <select
          v-model="newStatus"
          class="text-xs rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 py-1 px-2"
          :disabled="markResolved"
        >
          <option value="">— status —</option>
          <option v-for="s in STATUSES" :key="s" :value="s">
            {{ t(`feedback.status.${s}`) }}
          </option>
        </select>
      </template>
      <span v-else class="text-xs text-gray-500 dark:text-gray-400">
        {{ t('feedback.thread_reopen_hint') }}
      </span>
      <span class="text-xs text-gray-400 dark:text-gray-500 ml-auto">
        {{ body.length }} / {{ BODY_MAX }}
      </span>
      <button
        type="button"
        :disabled="!canSend"
        :class="[
          'inline-flex items-center gap-2 text-white text-sm font-medium px-3 py-1.5 rounded',
          canSend ? 'bg-blue-600 hover:bg-blue-700' : 'bg-blue-300 dark:bg-blue-800 cursor-not-allowed',
        ]"
        @click="onSend"
      >
        <PaperAirplaneIcon class="h-4 w-4" />
        {{ t('feedback.thread_reply') }}
      </button>
    </div>
    <div v-if="asAdmin && internal" class="mt-2 text-xs text-amber-700 dark:text-amber-400">
      {{ t('admin.feedback.internal_hint') }}
    </div>
  </div>
</template>
