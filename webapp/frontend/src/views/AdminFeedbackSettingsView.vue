<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { PaperAirplaneIcon } from '@heroicons/vue/24/outline'
import AdminNav from '../components/AdminNav.vue'
import {
  getAdminFeedbackSettings, setAdminFeedbackSettings, forceFeedbackDigest,
  type AdminFeedbackSettings,
} from '../api'

const { t } = useI18n()

const settings = ref<AdminFeedbackSettings | null>(null)
const recipientsText = ref('')
const saving = ref(false)
const savedFlash = ref(false)
const errorMsg = ref('')
const forceMsg = ref('')

const INTERVALS = [0, 1, 3, 6, 12, 24]
const BATCHES = [1, 3, 5]

async function load() {
  const r = await getAdminFeedbackSettings()
  settings.value = r.data
  recipientsText.value = r.data.extra_recipients.join(', ')
}

async function save() {
  if (!settings.value) return
  saving.value = true
  errorMsg.value = ''
  const list = recipientsText.value.split(',').map(e => e.trim()).filter(Boolean)
  try {
    await setAdminFeedbackSettings({
      digest_interval_hours: settings.value.digest_interval_hours,
      min_batch_size: settings.value.min_batch_size,
      urgent_bypass: settings.value.urgent_bypass,
      extra_recipients: list,
    })
    savedFlash.value = true
    setTimeout(() => (savedFlash.value = false), 2000)
    await load()
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.detail || 'Failed to save'
  } finally {
    saving.value = false
  }
}

async function forceSend() {
  if (!confirm(t('admin.feedback.force_send_confirm'))) return
  forceMsg.value = ''
  try {
    const r = await forceFeedbackDigest()
    forceMsg.value = t('admin.feedback.force_send_ok', { n: r.data.sent })
    await load()
  } catch (e: any) {
    if (e?.response?.status === 400) {
      forceMsg.value = t('admin.feedback.force_send_empty')
    } else {
      forceMsg.value = e?.response?.data?.detail || 'Failed'
    }
  }
}

function relTime(iso: string | null) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

onMounted(load)
</script>

<template>
  <div class="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
    <AdminNav />

    <div class="flex items-center gap-2 mt-4 mb-4">
      <router-link
        to="/admin/feedback"
        class="px-3 py-1.5 rounded-md text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
      >{{ t('admin.feedback.inbox_tab') }}</router-link>
      <router-link
        to="/admin/feedback/settings"
        class="px-3 py-1.5 rounded-md text-sm font-medium bg-blue-600 text-white"
      >{{ t('admin.feedback.settings_tab') }}</router-link>
    </div>

    <div v-if="!settings" class="text-sm text-gray-500 dark:text-gray-400">Loading…</div>

    <div v-else class="grid grid-cols-12 gap-6">
      <div class="col-span-12 lg:col-span-8 space-y-6">
        <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-5 space-y-5">
          <!-- Digest interval -->
          <div>
            <label class="block text-xs font-semibold text-gray-700 dark:text-gray-200 uppercase tracking-wide mb-1.5">
              {{ t('admin.feedback.digest_interval') }}
            </label>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="h in INTERVALS"
                :key="h"
                type="button"
                :class="[
                  'px-2.5 py-1 rounded-full text-xs font-medium border transition',
                  settings.digest_interval_hours === h
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600',
                ]"
                @click="settings.digest_interval_hours = h"
              >
                {{ h === 0 ? t('admin.feedback.digest_off') : t('admin.feedback.digest_every_hours', { h }) }}
              </button>
            </div>
          </div>

          <!-- Min batch -->
          <div>
            <label class="block text-xs font-semibold text-gray-700 dark:text-gray-200 uppercase tracking-wide mb-1.5">
              {{ t('admin.feedback.min_batch_size') }}
            </label>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="b in BATCHES"
                :key="b"
                type="button"
                :class="[
                  'px-3 py-1 rounded-full text-xs font-medium border transition',
                  settings.min_batch_size === b
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600',
                ]"
                @click="settings.min_batch_size = b"
              >{{ b }}</button>
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ t('admin.feedback.min_batch_size_hint') }}</p>
          </div>

          <!-- Urgent bypass -->
          <label class="flex items-center gap-2 cursor-pointer">
            <input
              v-model="settings.urgent_bypass"
              type="checkbox"
              class="h-4 w-4 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
            />
            <span class="text-sm text-gray-800 dark:text-gray-200">{{ t('admin.feedback.urgent_bypass') }}</span>
          </label>

          <!-- Extra recipients -->
          <div>
            <label class="block text-xs font-semibold text-gray-700 dark:text-gray-200 uppercase tracking-wide mb-1.5">
              {{ t('admin.feedback.recipients') }}
            </label>
            <input
              v-model="recipientsText"
              type="text"
              placeholder="ops@example.com, moderator@example.com"
              class="block w-full border border-gray-300 dark:border-gray-600 rounded-md py-2 px-3 text-sm bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 font-mono"
            />
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">{{ t('admin.feedback.recipients_hint') }}</p>
          </div>

          <div v-if="errorMsg" class="text-sm text-red-600 dark:text-red-400">{{ errorMsg }}</div>

          <div class="flex items-center gap-3 pt-2 border-t border-gray-100 dark:border-gray-700">
            <button
              type="button"
              :disabled="saving"
              :class="[
                'inline-flex items-center text-white text-sm font-medium px-4 py-2 rounded',
                saving ? 'bg-blue-300 dark:bg-blue-800 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-700',
              ]"
              @click="save"
            >
              {{ t('admin.feedback.save') }}
            </button>
            <span v-if="savedFlash" class="text-sm text-emerald-600 dark:text-emerald-400">{{ t('admin.feedback.saved') }}</span>
          </div>
        </div>
      </div>

      <aside class="col-span-12 lg:col-span-4 space-y-4">
        <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">{{ t('admin.feedback.last_digest') }}</span>
            <span class="text-sm font-mono text-gray-800 dark:text-gray-200">{{ relTime(settings.last_digest_at) }}</span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">{{ t('admin.feedback.next_eligible') }}</span>
            <span class="text-sm font-mono text-gray-800 dark:text-gray-200">{{ relTime(settings.next_eligible_at) }}</span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">{{ t('admin.feedback.pending') }}</span>
            <span class="text-sm font-mono text-gray-800 dark:text-gray-200">{{ settings.pending_count }}</span>
          </div>
        </div>

        <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
          <button
            type="button"
            class="w-full inline-flex items-center justify-center gap-2 text-sm font-medium px-3 py-2 rounded border border-blue-500 text-blue-600 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/30"
            @click="forceSend"
          >
            <PaperAirplaneIcon class="h-4 w-4" />
            {{ t('admin.feedback.force_send') }}
          </button>
          <div v-if="forceMsg" class="mt-2 text-xs text-gray-600 dark:text-gray-300">{{ forceMsg }}</div>
        </div>
      </aside>
    </div>
  </div>
</template>
