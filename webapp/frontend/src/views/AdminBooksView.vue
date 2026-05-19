<script setup lang="ts">
import { ref, onMounted, inject, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api from '../api'
import AdminNav from '../components/AdminNav.vue'
import BookMetadataEditor from '../components/BookMetadataEditor.vue'

const { t } = useI18n({ useScope: 'global' })

type CurrentUser = { email: string, is_admin?: boolean } | null
const currentUser = inject<{ value: CurrentUser } | null>('currentUser', null)
const router = useRouter()
const route = useRoute()

type Match = {
  hash_id: string
  path: string
  parent_dir?: string
  cover_url?: string | null
  title?: string | null
  author?: string | null
  description?: string | null
  clearance: number
}

const query = ref('')
const page = ref(1)
const matches = ref<Match[]>([])
const total = ref(0)
const totalPages = ref(0)
const searching = ref(false)
const searchError = ref('')

const editingId = ref<string | null>(null)

const doSearch = async (resetPage = true) => {
  if (resetPage) page.value = 1
  searching.value = true
  searchError.value = ''
  try {
    const res = await api.get('/search', { params: { q: query.value, page: page.value, per_page: 50 } })
    matches.value = res.data.matches
    total.value = res.data.total ?? 0
    totalPages.value = res.data.total_pages ?? 0
  } catch (err: any) {
    searchError.value = err.response?.data?.detail || err.message
  } finally {
    searching.value = false
  }
}

const goPage = async (p: number) => {
  page.value = p
  await doSearch(false)
}

const openEditor = (m: Match) => {
  editingId.value = m.hash_id
}

const closeEditor = () => {
  editingId.value = null
  // Drop the ?hash= query param so a reload doesn't reopen the editor.
  if (route.query.hash) router.replace({ query: { ...route.query, hash: undefined } })
}

const onSaved = (updated: any) => {
  const row = matches.value.find(m => m.hash_id === updated.id)
  if (row) {
    row.title = updated.title
    row.author = updated.author
    row.clearance = updated.clearance
  }
}

const deleteBook = async (m: Match) => {
  const ok = window.confirm(t('admin.delete_confirm', {
    title: m.title || m.path,
    prefix: m.hash_id.slice(0, 12),
  }))
  if (!ok) return
  try {
    const res = await api.delete(`/admin/books/${encodeURIComponent(m.hash_id)}`)
    matches.value = matches.value.filter(x => x.hash_id !== m.hash_id)
    total.value = Math.max(0, total.value - 1)
    if (res.data.errors?.length) {
      alert(t('admin.delete_fs_warnings', { errors: res.data.errors.join('\n') }))
    }
  } catch (err: any) {
    alert(err.response?.data?.detail || err.message)
  }
}

const openFromQuery = () => {
  const q = route.query.hash
  if (typeof q === 'string' && q) editingId.value = q
}

onMounted(() => {
  if (!currentUser?.value?.is_admin) {
    router.replace('/')
    return
  }
  openFromQuery()
})

watch(() => route.query.hash, openFromQuery)
</script>

<template>
  <div class="space-y-6 max-w-6xl mx-auto">
    <AdminNav />

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6 space-y-4">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-white">{{ t('admin.find_book') }}</h2>
      <form @submit.prevent="doSearch(true)" class="flex gap-2">
        <input
          v-model="query"
          type="search"
          :placeholder="t('admin.search_placeholder')"
          class="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
        />
        <button
          type="submit"
          :disabled="searching"
          class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 focus:outline-none"
        >{{ searching ? t('admin.searching') : t('admin.search') }}</button>
      </form>
      <i18n-t keypath="admin.hint" tag="p" class="text-xs text-gray-500 dark:text-gray-400">
        <template #pathExample>
          <code class="bg-gray-100 dark:bg-gray-700 px-1 rounded">path:Law/</code>
        </template>
        <template #extExample>
          <code class="bg-gray-100 dark:bg-gray-700 px-1 rounded">ext:pdf</code>
        </template>
        <template #needsReviewExample>
          <code class="bg-gray-100 dark:bg-gray-700 px-1 rounded">needs_review:1</code>
        </template>
      </i18n-t>
      <div v-if="searchError" class="text-red-600 dark:text-red-400 text-sm">{{ searchError }}</div>

      <div v-if="matches.length" class="space-y-2">
        <p class="text-sm text-gray-600 dark:text-gray-300">{{ t('admin.match_count', { count: total }) }}</p>
        <ul class="divide-y divide-gray-100 dark:divide-gray-700">
          <li v-for="m in matches" :key="m.hash_id" class="py-3 flex items-start gap-4">
            <div class="flex-1 min-w-0">
              <p class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate" :title="m.title || m.path">{{ m.title || m.path }}</p>
              <p v-if="m.author" class="text-xs text-gray-600 dark:text-gray-300 truncate">{{ m.author }}</p>
              <router-link
                :to="`/item/${m.path}`"
                target="_blank"
                class="block text-xs text-blue-600 dark:text-blue-400 hover:underline truncate"
                :title="m.path"
              >{{ m.path }}</router-link>
              <p class="text-xs text-gray-400 dark:text-gray-500 font-mono truncate" :title="m.hash_id">{{ m.hash_id.slice(0, 16) }}…</p>
            </div>
            <span class="px-2 py-0.5 rounded text-xs font-mono bg-amber-100 dark:bg-amber-900/70 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-700 self-center">
              🔒 {{ m.clearance }}
            </span>
            <button
              @click="openEditor(m)"
              class="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 focus:outline-none"
            >{{ t('admin.edit') }}</button>
            <button
              @click="deleteBook(m)"
              class="px-3 py-1 text-sm bg-red-600 text-white rounded hover:bg-red-700 focus:outline-none"
            >{{ t('admin.delete') }}</button>
          </li>
        </ul>

        <div v-if="totalPages > 1" class="flex items-center justify-between text-sm pt-2">
          <button
            :disabled="page <= 1"
            @click="goPage(page - 1)"
            class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40 focus:outline-none"
          >{{ t('admin.prev') }}</button>
          <span class="text-gray-600 dark:text-gray-300">{{ t('admin.page_of', { page, total: totalPages }) }}</span>
          <button
            :disabled="page >= totalPages"
            @click="goPage(page + 1)"
            class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40 focus:outline-none"
          >{{ t('admin.next') }}</button>
        </div>
      </div>
      <p v-else-if="!searching && query" class="text-sm text-gray-500 dark:text-gray-400">{{ t('admin.no_matches') }}</p>
    </section>

    <BookMetadataEditor :hash-id="editingId" @close="closeEditor" @saved="onSaved" />
  </div>
</template>
