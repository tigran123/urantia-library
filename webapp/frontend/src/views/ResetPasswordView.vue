<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { EyeIcon, EyeSlashIcon } from '@heroicons/vue/24/outline'
import api from '../api'

const { t, locale } = useI18n({ useScope: 'global' })

// Mirror of the server-side MIN_PASSWORD_LENGTH (config.py) for inline feedback;
// the backend is the authority and re-checks.
const MIN_PASSWORD_LENGTH = 8

const password = ref('')
const confirmPassword = ref('')
const showPassword = ref(false)
const showConfirmPassword = ref(false)
const token = ref('')
const errorMsg = ref('')
const successMsg = ref('')
const loading = ref(false)

const route = useRoute()

onMounted(() => {
  if (route.query.token) {
    token.value = route.query.token as string
  } else {
    errorMsg.value = t('auth.setPasswordInvalidToken')
  }
})

const handleReset = async () => {
  if (password.value.length < MIN_PASSWORD_LENGTH) {
    errorMsg.value = t('auth.resetPasswordTooShort')
    return
  }
  if (password.value !== confirmPassword.value) {
    errorMsg.value = t('auth.setPasswordMismatch')
    return
  }

  errorMsg.value = ''
  successMsg.value = ''
  loading.value = true

  try {
    await api.post('/reset-password', {
      token: token.value,
      password: password.value,
    })
    successMsg.value = t('auth.resetPasswordSuccess')
    password.value = ''
    confirmPassword.value = ''
  } catch (err: any) {
    // Always show the localized message — never surface the backend's English
    // `detail`. The only server errors reaching here (invalid/expired token, the
    // length check, and 429) all map to this string, and the client pre-validates
    // length, so a Russian user never sees English error text.
    errorMsg.value = t('auth.resetPasswordError')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div :key="locale" class="max-w-md mx-auto mt-10 bg-white dark:bg-gray-800 p-8 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm">
    <h2 class="text-2xl font-bold mb-6 text-center text-gray-900 dark:text-white">{{ t('auth.resetPasswordTitle') }}</h2>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800 rounded text-sm">
      {{ errorMsg }}
    </div>

    <div v-if="successMsg" class="mb-4 p-3 bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800 rounded text-sm">
      {{ successMsg }}
      <div class="mt-4">
        <router-link :to="{ name: 'login' }" class="text-blue-600 hover:underline">{{ t('auth.setPasswordLoginLink') }}</router-link>
      </div>
    </div>

    <form v-if="!successMsg && token" @submit.prevent="handleReset" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('auth.setPasswordNewLabel') }}</label>
        <div class="relative">
          <input
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            autocomplete="new-password"
            required
            class="w-full px-3 py-2 pr-10 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 rounded-md focus:ring-blue-500 focus:border-blue-500"
          />
          <button
            type="button"
            @click="showPassword = !showPassword"
            class="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus:outline-none"
          >
            <EyeSlashIcon v-if="showPassword" class="h-5 w-5" />
            <EyeIcon v-else class="h-5 w-5" />
          </button>
        </div>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('auth.setPasswordConfirmLabel') }}</label>
        <div class="relative">
          <input
            v-model="confirmPassword"
            :type="showConfirmPassword ? 'text' : 'password'"
            autocomplete="new-password"
            required
            class="w-full px-3 py-2 pr-10 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 rounded-md focus:ring-blue-500 focus:border-blue-500"
          />
          <button
            type="button"
            @click="showConfirmPassword = !showConfirmPassword"
            class="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus:outline-none"
          >
            <EyeSlashIcon v-if="showConfirmPassword" class="h-5 w-5" />
            <EyeIcon v-else class="h-5 w-5" />
          </button>
        </div>
      </div>

      <button
        type="submit"
        :disabled="loading"
        class="w-full mt-4 bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {{ loading ? t('auth.resetPasswordLoading') : t('auth.resetPasswordBtn') }}
      </button>
    </form>

    <div v-if="!successMsg" class="mt-6 text-center text-sm text-gray-600 dark:text-gray-400">
      <router-link :to="{ name: 'login' }" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('auth.forgotPasswordBackToLogin') }}</router-link>
    </div>
  </div>
</template>
