<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { EyeIcon, EyeSlashIcon } from '@heroicons/vue/24/outline'
import api from '../api'

const { t, locale } = useI18n({ useScope: 'global' })

const realName = ref('')
const password = ref('')
const confirmPassword = ref('')
const showPassword = ref(false)
const showConfirmPassword = ref(false)
const token = ref('')
const errorMsg = ref('')
const successMsg = ref('')
const loading = ref(false)
const acceptedLegal = ref(false)

const route = useRoute()

onMounted(() => {
  if (route.query.token) {
    token.value = route.query.token as string
  } else {
    errorMsg.value = t('auth.setPasswordInvalidToken')
  }
})

const handleSetPassword = async () => {
  if (password.value !== confirmPassword.value) {
    errorMsg.value = t('auth.setPasswordMismatch')
    return
  }

  errorMsg.value = ''
  successMsg.value = ''
  loading.value = true

  try {
    await api.post('/set-password', {
      token: token.value,
      password: password.value,
      real_name: realName.value.trim() || null,
      accepted_legal: acceptedLegal.value,
    })
    successMsg.value = t('auth.setPasswordSuccess')
    password.value = ''
    confirmPassword.value = ''
  } catch (err: any) {
    if (err.response && err.response.data && err.response.data.detail) {
      errorMsg.value = err.response.data.detail
    } else {
      errorMsg.value = t('auth.setPasswordError')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div :key="locale" class="max-w-md mx-auto mt-10 bg-white p-8 border rounded-lg shadow-sm">
    <h2 class="text-2xl font-bold mb-6 text-center text-gray-900">{{ t('auth.setPasswordTitle') }}</h2>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
      {{ errorMsg }}
    </div>

    <div v-if="successMsg" class="mb-4 p-3 bg-green-50 text-green-700 rounded text-sm">
      {{ successMsg }}
      <div class="mt-4">
        <router-link :to="{ name: 'login' }" class="text-blue-600 hover:underline">{{ t('auth.setPasswordLoginLink') }}</router-link>
      </div>
    </div>

    <form v-if="!successMsg && token" @submit.prevent="handleSetPassword" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">{{ t('auth.setPasswordFullNameLabel') }} <span class="text-gray-400 font-normal">{{ t('auth.setPasswordOptional') }}</span></label>
        <input
          v-model="realName"
          type="text"
          maxlength="100"
          autocomplete="name"
          class="w-full px-3 py-2 border rounded-md focus:ring-blue-500 focus:border-blue-500"
        />
        <p class="text-xs text-gray-500 mt-1">{{ t('auth.setPasswordFullNameHelp') }}</p>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">{{ t('auth.setPasswordNewLabel') }}</label>
        <div class="relative">
          <input
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            required
            class="w-full px-3 py-2 pr-10 border rounded-md focus:ring-blue-500 focus:border-blue-500"
          />
          <button
            type="button"
            @click="showPassword = !showPassword"
            class="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600 focus:outline-none"
          >
            <EyeSlashIcon v-if="showPassword" class="h-5 w-5" />
            <EyeIcon v-else class="h-5 w-5" />
          </button>
        </div>
      </div>

      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">{{ t('auth.setPasswordConfirmLabel') }}</label>
        <div class="relative">
          <input
            v-model="confirmPassword"
            :type="showConfirmPassword ? 'text' : 'password'"
            required
            class="w-full px-3 py-2 pr-10 border rounded-md focus:ring-blue-500 focus:border-blue-500"
          />
          <button
            type="button"
            @click="showConfirmPassword = !showConfirmPassword"
            class="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600 focus:outline-none"
          >
            <EyeSlashIcon v-if="showConfirmPassword" class="h-5 w-5" />
            <EyeIcon v-else class="h-5 w-5" />
          </button>
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
                <router-link :to="{ name: 'privacy' }" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.legal.privacy_instr') }}</router-link>
              </template>
              <template #terms>
                <router-link :to="{ name: 'terms' }" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline">{{ t('app.legal.terms_instr') }}</router-link>
              </template>
            </i18n-t>
          </span>
        </label>
      </div>

      <button
        type="submit"
        :disabled="loading || !acceptedLegal"
        class="w-full mt-4 bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {{ loading ? t('auth.setPasswordLoading') : t('auth.setPasswordBtn') }}
      </button>
    </form>
  </div>
</template>
