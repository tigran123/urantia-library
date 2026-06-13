<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import AdminNav from '../components/AdminNav.vue'
import { adminListComments, adminApproveComment, adminDeleteComment, type AdminCommentItem } from '../api'

const { t } = useI18n({ useScope: 'global' })

const PER_PAGE = 50

const comments = ref<AdminCommentItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(true)
const loadingMore = ref(false)
const error = ref('')
const filter = ref<'pending' | 'recent'>('pending')
const busyId = ref<number | null>(null)

const hasMore = computed(() => comments.value.length < total.value)

const load = async () => {
  loading.value = true
  error.value = ''
  page.value = 1
  try {
    const res = await adminListComments(filter.value, 1, PER_PAGE)
    comments.value = res.data.comments
    total.value = res.data.total
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

const loadMore = async () => {
  loadingMore.value = true
  try {
    const next = page.value + 1
    const res = await adminListComments(filter.value, next, PER_PAGE)
    comments.value = [...comments.value, ...res.data.comments]
    total.value = res.data.total
    page.value = next
  } catch (err: any) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loadingMore.value = false
  }
}

const setFilter = (f: 'pending' | 'recent') => {
  filter.value = f
  load()
}

const approve = async (c: AdminCommentItem) => {
  busyId.value = c.id
  try {
    await adminApproveComment(c.id)
    await load()
  } catch (err: any) {
    alert(err.response?.data?.detail || err.message)
  } finally {
    busyId.value = null
  }
}

const remove = async (c: AdminCommentItem) => {
  if (!confirm(t('admin.comments.delete_confirm'))) return
  busyId.value = c.id
  try {
    await adminDeleteComment(c.id)
    await load()
  } catch (err: any) {
    alert(err.response?.data?.detail || err.message)
  } finally {
    busyId.value = null
  }
}

const formatDate = (s: string) => (s ? new Date(s).toLocaleString() : '')

// Admin access is enforced by the router's /admin/* beforeEach guard.
onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <AdminNav />

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <div class="flex items-center justify-between mb-4 gap-4 flex-wrap">
        <h2 class="text-lg font-semibold text-gray-900 dark:text-white">{{ t('admin.comments.title') }}</h2>
        <div class="flex gap-2">
          <button
            @click="setFilter('pending')"
            class="px-3 py-1 rounded text-sm font-medium"
            :class="filter === 'pending' ? 'bg-blue-600 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300'"
          >{{ t('admin.comments.filter_pending') }}</button>
          <button
            @click="setFilter('recent')"
            class="px-3 py-1 rounded text-sm font-medium"
            :class="filter === 'recent' ? 'bg-blue-600 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300'"
          >{{ t('admin.comments.filter_recent') }}</button>
        </div>
      </div>

      <div v-if="loading" class="text-gray-500 dark:text-gray-400">{{ t('admin.loading') }}</div>
      <div v-else-if="error" class="text-red-600 dark:text-red-400">{{ error }}</div>
      <p v-else-if="!comments.length" class="text-gray-500 dark:text-gray-400">{{ t('admin.comments.empty') }}</p>
      <template v-else>
        <ul class="divide-y divide-gray-100 dark:divide-gray-700">
          <li v-for="c in comments" :key="c.id" class="py-4">
            <div class="flex items-start justify-between gap-4">
              <div class="min-w-0">
                <div class="flex items-center gap-2 flex-wrap text-xs text-gray-500 dark:text-gray-400">
                  <span class="font-semibold text-gray-900 dark:text-gray-100">{{ c.author_name }}</span>
                  <span v-if="c.parent_id" class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700">{{ t('admin.comments.reply_badge') }}</span>
                  <span>{{ formatDate(c.created_at) }}</span>
                  <span
                    class="px-1.5 py-0.5 rounded"
                    :class="c.status === 'approved'
                      ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                      : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'"
                  >{{ c.status }}</span>
                </div>
                <p class="mt-0.5 text-sm text-gray-400 dark:text-gray-500 truncate">
                  {{ t('admin.comments.on_book') }}:
                  <router-link
                    v-if="c.book_path"
                    :to="`/item/${c.book_path}`"
                    target="_blank"
                    class="text-blue-600 dark:text-blue-400 hover:underline"
                  >{{ c.book_title || c.hash_id }}</router-link>
                  <span v-else>{{ c.book_title || c.hash_id }}</span>
                </p>
                <p v-if="c.parent_snippet" class="mt-1 text-xs italic text-gray-400 dark:text-gray-500 border-l-2 border-gray-200 dark:border-gray-600 pl-2">
                  {{ c.parent_snippet }}
                </p>
                <p class="mt-1 text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{{ c.body }}</p>
              </div>
              <div class="flex-shrink-0 flex gap-2">
                <button
                  v-if="c.status !== 'approved'"
                  @click="approve(c)"
                  :disabled="busyId === c.id"
                  class="px-3 py-1 rounded text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
                >{{ t('admin.comments.approve') }}</button>
                <button
                  @click="remove(c)"
                  :disabled="busyId === c.id"
                  class="px-3 py-1 rounded text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                >{{ t('admin.comments.delete') }}</button>
              </div>
            </div>
          </li>
        </ul>

        <div class="mt-4 flex items-center justify-center gap-4 text-sm text-gray-500 dark:text-gray-400">
          <span>{{ t('admin.comments.shown_count', { shown: comments.length, total }) }}</span>
          <button
            v-if="hasMore"
            @click="loadMore()"
            :disabled="loadingMore"
            class="px-4 py-1.5 rounded-md font-medium bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50"
          >{{ loadingMore ? t('admin.loading') : t('admin.comments.load_more') }}</button>
        </div>
      </template>
    </section>
  </div>
</template>
