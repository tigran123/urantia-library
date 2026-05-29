<script setup lang="ts">
// Fired by App.vue whenever the signed-in user's legal_acceptance_current
// flips to false (LEGAL_VERSION in database.py advanced past whatever they
// last accepted). Blocking overlay — only "I accept" or "Sign out" closes it.
// No X button, no escape-key close: per the design plan this is a forced
// acknowledgement, not a dismissible banner.
//
// Copy mirrors the registration form's acceptance block (RegisterView.vue),
// reusing auth.acceptLegal-style i18n slots so the wording lines up visually.

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { acceptLegal } from '../api'

const { t } = useI18n({ useScope: 'global' })

const emit = defineEmits<{
  (e: 'accepted', user: import('../api').CurrentUser): void
  (e: 'signout'): void
}>()

const submitting = ref(false)
const error = ref('')

const onAccept = async () => {
  if (submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const res = await acceptLegal()
    emit('accepted', res.data)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || 'error'
  } finally {
    submitting.value = false
  }
}

const onSignout = () => emit('signout')
</script>

<template>
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
    role="dialog"
    aria-modal="true"
    :aria-labelledby="'reaccept-title'"
  >
    <div class="max-w-lg w-full bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 p-6">
      <h2 id="reaccept-title" class="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-3">
        {{ t('auth.reacceptTitle') }}
      </h2>

      <p class="text-sm text-gray-700 dark:text-gray-300 mb-5">
        <i18n-t keypath="auth.reacceptBody" tag="span">
          <template #privacy>
            <router-link :to="{ name: 'privacy' }" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.legal.privacy_acc') }}</router-link>
          </template>
          <template #terms>
            <router-link :to="{ name: 'terms' }" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.legal.terms_acc') }}</router-link>
          </template>
        </i18n-t>
      </p>

      <p v-if="error" class="text-sm text-red-600 dark:text-red-400 mb-3">{{ error }}</p>

      <div class="flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
        <button
          type="button"
          @click="onSignout"
          class="px-4 py-2 rounded-md text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-600"
        >
          {{ t('auth.reacceptSignout') }}
        </button>
        <button
          type="button"
          @click="onAccept"
          :disabled="submitting"
          class="px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{ t('auth.reacceptButton') }}
        </button>
      </div>
    </div>
  </div>
</template>
