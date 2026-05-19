<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { FolderIcon } from '@heroicons/vue/24/outline'
import api from '../../api'

const { t } = useI18n({ useScope: 'global' })

const props = defineProps<{
  topDir: string
  subpath: string
  filename: string
  tops: string[]
}>()

const emit = defineEmits<{
  (e: 'update:topDir', v: string): void
  (e: 'update:subpath', v: string): void
}>()

const existing = ref<string[]>([])
let timer: number | null = null

const loadDirs = async () => {
  try {
    const res = await api.get('/admin/dirs', { params: { top: props.topDir || '' } })
    existing.value = res.data?.dirs || []
  } catch {
    existing.value = []
  }
}

watch(() => props.topDir, () => {
  loadDirs()
}, { immediate: true })

watch(() => props.subpath, () => {
  if (timer !== null) window.clearTimeout(timer)
  timer = window.setTimeout(loadDirs, 200)
})

const suggestions = computed(() => {
  const q = (props.subpath || '').trim().toLowerCase()
  if (!q) return []
  return existing.value
    .filter((d) => d.toLowerCase() !== q && d.toLowerCase().includes(q))
    .slice(0, 8)
})

const finalPath = computed(() => {
  const top = (props.topDir || '').replace(/^\/+|\/+$/g, '')
  const sub = (props.subpath || '').replace(/^\/+|\/+$/g, '')
  const file = props.filename || ''
  const parts = [top, sub].filter(Boolean)
  return parts.length ? `/${parts.join('/')}/${file}` : `/${file}`
})

const onTopDirChange = (e: Event) => emit('update:topDir', (e.target as HTMLSelectElement).value)
const onSubpathInput = (e: Event) => emit('update:subpath', (e.target as HTMLInputElement).value)
const pickSuggestion = (s: string) => emit('update:subpath', s)
</script>

<template>
  <div>
    <div class="flex items-center gap-2">
      <select
        :value="topDir"
        @change="onTopDirChange"
        class="appearance-none pl-3 pr-9 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
      >
        <option value="">/</option>
        <option v-for="top in tops" :key="top" :value="top">/{{ top }}</option>
      </select>
      <span class="font-mono text-gray-400 dark:text-gray-500">/</span>
      <input
        :value="subpath"
        @input="onSubpathInput"
        type="text"
        :placeholder="t('admin.upload.review.subpath_placeholder')"
        class="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
      />
    </div>
    <div class="mt-2 text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5">
      <FolderIcon class="w-3.5 h-3.5" />
      <span>{{ t('admin.upload.review.final_location') }}</span>
      <code class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 font-mono text-[11px] break-all">{{ finalPath }}</code>
    </div>
    <div v-if="suggestions.length" class="mt-2 flex flex-wrap gap-1.5 items-center">
      <span class="text-xs text-gray-400 dark:text-gray-500">{{ t('admin.upload.review.existing_subdirs') }}</span>
      <button
        v-for="s in suggestions"
        :key="s"
        type="button"
        @click="pickSuggestion(s)"
        class="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 font-mono hover:bg-blue-100 dark:hover:bg-blue-900/40 hover:text-blue-700 dark:hover:text-blue-300"
      >{{ s }}</button>
    </div>
  </div>
</template>
