<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api'

const { t, locale } = useI18n({ useScope: 'global' })

const email = ref('')
const errorMsg = ref('')
const successMsg = ref('')
const loading = ref(false)

const handleSubmit = async () => {
  errorMsg.value = ''
  successMsg.value = ''
  loading.value = true
  try {
    // The current UI locale is forwarded so the reset email matches the user's
    // language. The backend always returns the same generic response whether or
    // not the email is registered (anti-enumeration), so on success we show the
    // generic confirmation regardless.
    await api.post('/forgot-password', {
      email: email.value,
      language: locale.value,
    })
    successMsg.value = t('auth.forgotPasswordSuccess')
    email.value = ''
  } catch (err: any) {
    errorMsg.value = t('auth.forgotPasswordError')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div :key="locale" class="max-w-md mx-auto mt-10 bg-white dark:bg-gray-800 p-8 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm">
    <h2 class="text-2xl font-bold mb-2 text-center text-gray-900 dark:text-white">{{ t('auth.forgotPasswordTitle') }}</h2>
    <p class="text-sm text-center text-gray-600 dark:text-gray-400 mb-6">{{ t('auth.forgotPasswordSubtitle') }}</p>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800 rounded text-sm">
      {{ errorMsg }}
    </div>

    <div v-if="successMsg" class="mb-4 p-3 bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800 rounded text-sm">
      {{ successMsg }}
    </div>

    <form v-if="!successMsg" @submit.prevent="handleSubmit" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('auth.emailLabel') }}</label>
        <input
          v-model="email"
          type="email"
          autocomplete="username"
          required
          class="w-full px-3 py-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 rounded-md focus:ring-blue-500 focus:border-blue-500"
        />
      </div>

      <button
        type="submit"
        :disabled="loading"
        class="w-full bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
      >
        {{ loading ? t('auth.forgotPasswordLoading') : t('auth.forgotPasswordBtn') }}
      </button>
    </form>

    <div class="mt-6 text-center text-sm text-gray-600 dark:text-gray-400">
      <router-link :to="{ name: 'login' }" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('auth.forgotPasswordBackToLogin') }}</router-link>
    </div>
  </div>
</template>
