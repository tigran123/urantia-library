<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { EyeIcon, EyeSlashIcon } from '@heroicons/vue/24/outline'
import api from '../api'
import { useI18n } from 'vue-i18n'

const { t } = useI18n({ useScope: 'global' })
const email = ref('')
const password = ref('')
const showPassword = ref(false)
const errorMsg = ref('')
const loading = ref(false)
const router = useRouter()
const route = useRoute()

// Same-origin path validator. Accepts only "/foo"-shaped paths to defend
// against open-redirect via `?next=https://evil.test/...` or `?next=//evil`.
function safeNext(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  if (!raw.startsWith('/')) return null
  if (raw.startsWith('//')) return null   // protocol-relative URL
  if (raw.includes('://')) return null    // belt and braces
  return raw
}

const handleLogin = async () => {
  errorMsg.value = ''
  loading.value = true
  try {
    await api.post('/login', {
      email: email.value,
      password: password.value
    })
    // If the user was bounced here by the 401 interceptor, ?next holds the
    // original destination — return them there. Anything that fails the
    // same-origin validator falls back to the default landing page.
    const next = safeNext(route.query.next)
    if (next) {
      router.push(next)
    } else {
      router.push({ name: 'browse' })
    }
  } catch (err: any) {
    if (err.response && err.response.data && err.response.data.detail) {
      errorMsg.value = err.response.data.detail
    } else {
      errorMsg.value = t('auth.loginError')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-md mx-auto mt-10 bg-white dark:bg-gray-800 p-8 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm">
    <h2 class="text-2xl font-bold mb-6 text-center text-gray-900 dark:text-white">{{ t('auth.signInTitle') }}</h2>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800 rounded text-sm">
      {{ errorMsg }}
    </div>

    <form @submit.prevent="handleLogin" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('auth.emailLabel') }}</label>
        <input
          v-model="email"
          type="email"
          required
          class="w-full px-3 py-2 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 rounded-md focus:ring-blue-500 focus:border-blue-500"
        />
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('auth.passwordLabel') }}</label>
        <div class="relative">
          <input
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
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

      <button
        type="submit"
        :disabled="loading"
        class="w-full bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
      >
        {{ loading ? t('auth.signInLoading') : t('auth.signInBtn') }}
      </button>
    </form>

    <div class="mt-6 text-center text-sm text-gray-600 dark:text-gray-400">
      {{ t('auth.noAccount') }}
      <router-link :to="{ name: 'register' }" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('auth.requestAccessLink') }}</router-link>
    </div>

    <p class="mt-4 text-center text-xs text-gray-500 dark:text-gray-400">
      <i18n-t keypath="auth.loginLegalNote" tag="span">
        <template #privacy>
          <router-link :to="{ name: 'privacy' }" target="_blank" class="hover:underline">{{ t('app.legal.privacy_instr') }}</router-link>
        </template>
        <template #terms>
          <router-link :to="{ name: 'terms' }" target="_blank" class="hover:underline">{{ t('app.legal.terms_instr') }}</router-link>
        </template>
      </i18n-t>
    </p>
  </div>
</template>
