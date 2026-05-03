<script setup lang="ts">
import { ref } from 'vue'
import api from '../api'

const email = ref('')
const source = ref('')
const purpose = ref('')
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
      purpose: purpose.value || null
    })
    successMsg.value = 'Registration request queued! An admin will review it shortly. You will receive an email upon approval.'
    // Clear form
    email.value = ''
    source.value = ''
    purpose.value = ''
  } catch (err: any) {
    if (err.response && err.response.data && err.response.data.detail) {
      errorMsg.value = err.response.data.detail
    } else {
      errorMsg.value = 'An error occurred during registration.'
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="max-w-md mx-auto mt-10 bg-white p-8 border rounded-lg shadow-sm">
    <h2 class="text-2xl font-bold mb-6 text-center text-gray-900">Request Access</h2>

    <div v-if="errorMsg" class="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
      {{ errorMsg }}
    </div>

    <div v-if="successMsg" class="mb-4 p-3 bg-green-50 text-green-700 rounded text-sm">
      {{ successMsg }}
    </div>

    <form v-if="!successMsg" @submit.prevent="handleRegister" class="space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">Email *</label>
        <input
          v-model="email"
          type="email"
          required
          class="w-full px-3 py-2 border rounded-md focus:ring-blue-500 focus:border-blue-500"
        />
      </div>

      <div class="pt-4 border-t border-gray-100">
        <p class="text-xs text-gray-500 mb-4">
          <strong>Note:</strong> The following fields are optional, but omitting this context may cause delays in approving your registration.
        </p>

        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Where did you hear about the library?</label>
            <input
              v-model="source"
              type="text"
              placeholder="e.g., Friend, Google, Forum..."
              class="w-full px-3 py-2 border rounded-md focus:ring-blue-500 focus:border-blue-500"
            />
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Purpose for registering</label>
            <textarea
              v-model="purpose"
              rows="3"
              placeholder="Why would you like access to this library?"
              class="w-full px-3 py-2 border rounded-md focus:ring-blue-500 focus:border-blue-500"
            ></textarea>
          </div>
        </div>
      </div>

      <button
        type="submit"
        :disabled="loading"
        class="w-full mt-4 bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
      >
        {{ loading ? 'Submitting...' : 'Submit Request' }}
      </button>
    </form>

    <div class="mt-6 text-center text-sm text-gray-600">
      Already have an account?
      <router-link :to="{ name: 'login' }" class="text-blue-600 hover:underline">Sign In</router-link>
    </div>
  </div>
</template>
