<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { EyeIcon, EyeSlashIcon } from '@heroicons/vue/24/outline'
import api from '../api'

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
    errorMsg.value = 'Invalid or missing token.'
  }
})

const handleSetPassword = async () => {
  if (password.value !== confirmPassword.value) {
    errorMsg.value = 'Passwords do not match.'
    return
  }

  errorMsg.value = ''
  successMsg.value = ''
  loading.value = true

  try {
    await api.post('/set-password', {
      token: token.value,
      password: password.value,
    })
    successMsg.value = 'Password set successfully! You can now log in.'
    password.value = ''
    confirmPassword.value = ''
  } catch (err: any) {
    if (err.response && err.response.data && err.response.data.detail) {
      errorMsg.value = err.response.data.detail
    } else {
      errorMsg.value = 'An error occurred setting the password. The link might be expired.'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-md mx-auto mt-10 bg-white p-8 border rounded-lg shadow-sm">
    <h2 class="text-2xl font-bold mb-6 text-center text-gray-900">Set Your Password</h2>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
      {{ errorMsg }}
    </div>

    <div v-if="successMsg" class="mb-4 p-3 bg-green-50 text-green-700 rounded text-sm">
      {{ successMsg }}
      <div class="mt-4">
        <router-link :to="{ name: 'login' }" class="text-blue-600 hover:underline">Click here to log in</router-link>
      </div>
    </div>

    <form v-if="!successMsg && token" @submit.prevent="handleSetPassword" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">New Password</label>
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
        <label class="block text-sm font-medium text-gray-700 mb-1">Confirm Password</label>
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

      <button
        type="submit"
        :disabled="loading"
        class="w-full mt-4 bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
      >
        {{ loading ? 'Saving...' : 'Set Password' }}
      </button>
    </form>
  </div>
</template>
