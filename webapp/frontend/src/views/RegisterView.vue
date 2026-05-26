<script setup lang="ts">
import { ref } from 'vue'
import api from '../api'
import { useI18n } from 'vue-i18n'

const { t } = useI18n({ useScope: 'global' })
const email = ref('')
const source = ref('')
const purpose = ref('')
const acceptedLegal = ref(false)
const errorMsg = ref('')
const successMsg = ref('')
const loading = ref(false)

const handleRegister = async () => {
  errorMsg.value = ''
  successMsg.value = ''
  loading.value = true

  try {
    await api.post('/register', {
      email: email.value,
      source: source.value || null,
      purpose: purpose.value || null,
      accepted_legal: acceptedLegal.value
    })
    successMsg.value = t('auth.registerSuccess')
    // Clear form
    email.value = ''
    source.value = ''
    purpose.value = ''
  } catch (err: any) {
    if (err.response && err.response.data && err.response.data.detail) {
      errorMsg.value = err.response.data.detail
    } else {
      errorMsg.value = t('auth.registerError')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-md mx-auto mt-10 bg-white dark:bg-gray-800 p-8 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm">
    <h2 class="text-2xl font-bold mb-6 text-center text-gray-900 dark:text-white">{{ t('auth.registerTitle') }}</h2>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800 rounded text-sm">
      {{ errorMsg }}
    </div>

    <div v-if="successMsg" class="mb-4 p-3 bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800 rounded text-sm">
      {{ successMsg }}
    </div>

    <form v-if="!successMsg" @submit.prevent="handleRegister" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('auth.emailRequiredLabel') }}</label>
        <input
          v-model="email"
          type="email"
          required
          class="w-full px-3 py-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 rounded-md focus:ring-blue-500 focus:border-blue-500"
        />
      </div>

      <div class="pt-4 border-t border-gray-100 dark:border-gray-700">
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">
          <strong class="dark:text-gray-300">Note:</strong> {{ t('auth.optionalNote').replace('Note: ', '') }}
        </p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('auth.sourceLabel') }}</label>
            <input
              v-model="source"
              type="text"
              :placeholder="t('auth.sourcePlaceholder')"
              class="w-full px-3 py-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 rounded-md focus:ring-blue-500 focus:border-blue-500"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('auth.purposeLabel') }}</label>
            <textarea
              v-model="purpose"
              rows="3"
              :placeholder="t('auth.purposePlaceholder')"
              class="w-full px-3 py-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 rounded-md focus:ring-blue-500 focus:border-blue-500"
            ></textarea>
          </div>
        </div>
      </div>

      <div class="pt-4 border-t border-gray-100 dark:border-gray-700">
        <label class="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
          <input
            v-model="acceptedLegal"
            type="checkbox"
            required
            class="mt-0.5 h-4 w-4 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
          />
          <span>
            <i18n-t keypath="auth.acceptLegal" tag="span">
              <template #privacy>
                <router-link :to="{ name: 'privacy' }" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.legal.privacy') }}</router-link>
              </template>
              <template #terms>
                <router-link :to="{ name: 'terms' }" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.legal.terms') }}</router-link>
              </template>
            </i18n-t>
          </span>
        </label>
      </div>

      <button
        type="submit"
        :disabled="loading || !acceptedLegal"
        class="w-full mt-4 bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
      >
        {{ loading ? t('auth.submitLoading') : t('auth.submitBtn') }}
      </button>
    </form>

    <div class="mt-6 text-center text-sm text-gray-600 dark:text-gray-400">
      {{ t('auth.alreadyHaveAccount') }}
      <router-link :to="{ name: 'login' }" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('auth.signInTitle') }}</router-link>
    </div>
  </div>
</template>
