<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, inject, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api from '../api'
import AdminNav from '../components/AdminNav.vue'
import BookMetadataEditor from '../components/BookMetadataEditor.vue'
import DirectoryTreePicker from '../components/upload/DirectoryTreePicker.vue'

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
const searchInput = ref<HTMLInputElement | null>(null)
const page = ref(1)
const matches = ref<Match[]>([])
const total = ref(0)
const totalPages = ref(0)
const searching = ref(false)
const searchError = ref('')

const editingId = ref<string | null>(null)

type MoveItem = { src: string, dst: string, hash_id: string }
type MoveResponse = {
  src: string
  dst: string
  kind: 'file' | 'directory'
  dry_run: boolean
  moved: MoveItem[]
  errors: { path: string, reason: string }[]
  skipped: { path: string, reason: string }[]
}

const moveOpen = ref(false)
const moveSrc = ref('')
const moveDestParent = ref('')
const moveDestBasename = ref('')
const movePreview = ref<MoveResponse | null>(null)
const moveResult = ref<MoveResponse | null>(null)
const movePending = ref(false)
const moveError = ref('')

const moveSrcBasename = computed(() => {
  const s = (moveSrc.value || '').replace(/\/+$/, '')
  return s.split('/').pop() || ''
})

// Default the destination basename to the source basename whenever the source
// changes — admins usually want to keep the same name and just move the file.
watch(moveSrc, () => { moveDestBasename.value = moveSrcBasename.value })

const moveDst = computed(() => {
  const parts = [moveDestParent.value, moveDestBasename.value]
    .map((s) => (s || '').replace(/^\/+|\/+$/g, ''))
    .filter(Boolean)
  return parts.join('/')
})

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

const onDeleted = (hashId: string) => {
  matches.value = matches.value.filter(x => x.hash_id !== hashId)
  total.value = Math.max(0, total.value - 1)
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

const resetMoveResults = () => {
  movePreview.value = null
  moveResult.value = null
  moveError.value = ''
}

const movePreviewRun = async () => {
  if (!moveSrc.value.trim() || !moveDst.value.trim()) return
  movePending.value = true
  resetMoveResults()
  try {
    const res = await api.post<MoveResponse>('/admin/move', {
      src: moveSrc.value.trim(),
      dst: moveDst.value.trim(),
    }, { params: { dry_run: 1 } })
    movePreview.value = res.data
  } catch (err: any) {
    moveError.value = err.response?.data?.detail || err.message
  } finally {
    movePending.value = false
  }
}

const moveCommit = async () => {
  if (!moveSrc.value.trim() || !moveDst.value.trim()) return
  const n = movePreview.value?.moved.length ?? null
  const confirmMsg = n !== null
    ? t('admin.move.confirm_n', { n, src: moveSrc.value, dst: moveDst.value })
    : t('admin.move.confirm', { src: moveSrc.value, dst: moveDst.value })
  if (!window.confirm(confirmMsg)) return
  movePending.value = true
  resetMoveResults()
  try {
    const res = await api.post<MoveResponse>('/admin/move', {
      src: moveSrc.value.trim(),
      dst: moveDst.value.trim(),
    })
    moveResult.value = res.data
    if (res.data.moved.length > 0) {
      // Refresh search so any displayed row's path doesn't go stale.
      await doSearch(false)
    }
  } catch (err: any) {
    moveError.value = err.response?.data?.detail || err.message
  } finally {
    movePending.value = false
  }
}

const openMoveForRow = (m: Match) => {
  moveSrc.value = m.path
  moveDestParent.value = ''
  // basename auto-syncs via the watcher above
  moveOpen.value = true
  resetMoveResults()
  // Scroll the panel into view so the admin sees where the form is.
  nextTick(() => {
    const el = document.getElementById('reorganize-section')
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

const openFromQuery = () => {
  const q = route.query.hash
  if (typeof q === 'string' && q) editingId.value = q
}

// Ctrl/Cmd+X clears the search box and focuses it. If the user has text
// selected inside an editable field we yield to the native cut behaviour so
// they don't lose the cut affordance for incidental text-editing on the page.
const onShortcut = (e: KeyboardEvent) => {
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'x') return
  const active = document.activeElement as HTMLElement | null
  const inEditable = !!active && (
    active.tagName === 'INPUT' ||
    active.tagName === 'TEXTAREA' ||
    active.isContentEditable
  )
  const hasSelection = (window.getSelection()?.toString() || '').length > 0
  if (inEditable && hasSelection) return
  e.preventDefault()
  query.value = ''
  nextTick(() => searchInput.value?.focus())
}

onMounted(() => {
  if (!currentUser?.value?.is_admin) {
    router.replace('/')
    return
  }
  openFromQuery()
  window.addEventListener('keydown', onShortcut)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onShortcut)
})

watch(() => route.query.hash, openFromQuery)
</script>

<template>
  <div class="space-y-6 max-w-6xl mx-auto">
    <AdminNav />

    <section id="reorganize-section" class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6">
      <button
        type="button"
        @click="moveOpen = !moveOpen"
        class="w-full flex items-center justify-between text-left"
      >
        <h2 class="text-lg font-semibold text-gray-900 dark:text-white">{{ t('admin.move.heading') }}</h2>
        <span class="text-sm text-gray-500 dark:text-gray-400">{{ moveOpen ? '▾' : '▸' }}</span>
      </button>
      <div v-if="moveOpen" class="mt-4 space-y-4">
        <div>
          <span class="text-xs text-gray-500 dark:text-gray-400 block mb-1">{{ t('admin.move.from') }}</span>
          <DirectoryTreePicker
            v-model:selected-dir="moveSrc"
            :show-subpath="false"
            :show-final="false"
          />
          <p v-if="moveSrc" class="mt-1 text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5">
            <span>{{ t('admin.move.from') }}:</span>
            <code class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-[11px] break-all">/{{ moveSrc }}</code>
          </p>
        </div>
        <div>
          <span class="text-xs text-gray-500 dark:text-gray-400 block mb-1">{{ t('admin.move.to') }}</span>
          <DirectoryTreePicker
            v-model:selected-dir="moveDestParent"
            :show-subpath="false"
            :show-final="false"
          />
        </div>
        <label class="block">
          <span class="text-xs text-gray-500 dark:text-gray-400">{{ t('admin.move.dest_basename') }}</span>
          <input
            v-model="moveDestBasename"
            type="text"
            :placeholder="moveSrcBasename"
            class="w-full mt-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          />
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ t('admin.move.dest_basename_help') }}</p>
        </label>
        <p v-if="moveDst" class="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5">
          <span>{{ t('admin.move.to') }}:</span>
          <code class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-[11px] break-all">/{{ moveDst }}</code>
        </p>
        <div class="flex items-center justify-end gap-2">
          <button
            type="button"
            @click="movePreviewRun"
            :disabled="movePending || !moveSrc.trim() || !moveDst.trim()"
            class="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >{{ t('admin.move.preview') }}</button>
          <button
            type="button"
            @click="moveCommit"
            :disabled="movePending || !moveSrc.trim() || !moveDst.trim()"
            class="px-3 py-1.5 rounded bg-blue-600 text-white text-sm hover:bg-blue-700 disabled:opacity-50"
          >{{ t('admin.move.move') }}</button>
        </div>

        <div v-if="moveError" class="text-red-600 dark:text-red-400 text-sm">{{ moveError }}</div>

        <div v-if="movePreview" class="text-sm">
          <p class="text-gray-700 dark:text-gray-300">
            {{ t('admin.move.preview_count', { n: movePreview.moved.length, kind: movePreview.kind }) }}
          </p>
          <ul v-if="movePreview.moved.length" class="mt-2 space-y-0.5 max-h-48 overflow-y-auto text-xs font-mono text-gray-600 dark:text-gray-400">
            <li v-for="(it, i) in movePreview.moved.slice(0, 200)" :key="i" class="truncate">
              /{{ it.src }} → /{{ it.dst }}
            </li>
            <li v-if="movePreview.moved.length > 200" class="italic">… and {{ movePreview.moved.length - 200 }} more</li>
          </ul>
          <p v-if="movePreview.errors.length" class="mt-2 text-red-600 dark:text-red-400 text-xs">
            {{ t('admin.move.error_collision') }}
          </p>
          <ul v-if="movePreview.errors.length" class="mt-1 space-y-0.5 text-xs font-mono text-red-600 dark:text-red-400">
            <li v-for="(e, i) in movePreview.errors" :key="i" class="truncate">{{ e.path }} — {{ e.reason }}</li>
          </ul>
          <p v-if="movePreview.skipped.length" class="mt-2 text-gray-500 dark:text-gray-400 text-xs">
            {{ movePreview.skipped.map(s => `${s.path}: ${s.reason}`).join('; ') }}
          </p>
        </div>

        <div v-if="moveResult" class="text-sm">
          <p class="text-emerald-700 dark:text-emerald-300">
            {{ t('admin.move.moved_n', { n: moveResult.moved.length }) }}
          </p>
          <p v-if="moveResult.errors.length" class="mt-1 text-red-600 dark:text-red-400 text-xs">
            {{ moveResult.errors.length }} error(s):
          </p>
          <ul v-if="moveResult.errors.length" class="mt-1 space-y-0.5 text-xs font-mono text-red-600 dark:text-red-400">
            <li v-for="(e, i) in moveResult.errors" :key="i" class="truncate">{{ e.path }} — {{ e.reason }}</li>
          </ul>
        </div>
      </div>
    </section>

    <section class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 p-6 space-y-4">
      <h2 class="text-lg font-semibold text-gray-900 dark:text-white">{{ t('admin.find_book') }}</h2>
      <form @submit.prevent="doSearch(true)" class="flex gap-2">
        <input
          ref="searchInput"
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
              @click="openMoveForRow(m)"
              class="px-3 py-1 text-sm border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 rounded hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none"
            >{{ t('admin.move.row_action') }}</button>
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

    <BookMetadataEditor :hash-id="editingId" @close="closeEditor" @saved="onSaved" @deleted="onDeleted" />
  </div>
</template>
