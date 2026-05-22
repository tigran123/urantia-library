<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { FolderIcon } from '@heroicons/vue/24/outline'
import api from '../../api'
import TreeNode from './TreeNode.vue'

const { t } = useI18n({ useScope: 'global' })

const props = withDefaults(defineProps<{
  selectedDir: string
  extraSubpath?: string
  filename?: string
  showSubpath?: boolean
  showFinal?: boolean
}>(), {
  extraSubpath: '',
  filename: '',
  showSubpath: true,
  showFinal: true,
})

const emit = defineEmits<{
  (e: 'update:selectedDir', v: string): void
  (e: 'update:extraSubpath', v: string): void
}>()

const rootChildren = ref<string[]>([])
const rootLoading = ref(true)
const rootError = ref('')

const loadRoot = async () => {
  rootLoading.value = true
  rootError.value = ''
  try {
    const res = await api.get('/admin/dirs', { params: { path: '' } })
    rootChildren.value = res.data?.dirs || []
  } catch (e: any) {
    rootError.value = e?.response?.data?.detail || e?.message || 'Failed to load'
  } finally {
    rootLoading.value = false
  }
}

onMounted(loadRoot)

const onSelect = (path: string) => emit('update:selectedDir', path)
const onExtraInput = (e: Event) => emit('update:extraSubpath', (e.target as HTMLInputElement).value)
const onRootClick = () => emit('update:selectedDir', '')

const finalPath = computed(() => {
  const sel = (props.selectedDir || '').replace(/^\/+|\/+$/g, '')
  const extra = (props.extraSubpath || '').replace(/^\/+|\/+$/g, '')
  const parts = [sel, extra].filter(Boolean)
  const dir = parts.join('/')
  return dir ? `/${dir}/${props.filename}` : `/${props.filename}`
})
</script>

<template>
  <div>
    <div class="border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 max-h-72 overflow-auto p-1">
      <div v-if="rootLoading" class="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">{{ t('admin.upload.review.tree_loading') }}</div>
      <div v-else-if="rootError" class="px-3 py-2 text-xs text-red-600 dark:text-red-400">{{ rootError }}</div>
      <template v-else>
        <button
          type="button"
          @click="onRootClick"
          class="w-full flex items-center gap-2 px-2 py-1 rounded text-left font-mono text-sm"
          :class="selectedDir === ''
            ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200'
            : 'hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-800 dark:text-gray-200'"
        >
          <FolderIcon class="w-4 h-4 shrink-0" />
          <span>/</span>
          <span class="text-xs text-gray-500 dark:text-gray-400 ml-1">{{ t('admin.upload.review.tree_root') }}</span>
        </button>
        <TreeNode
          v-for="child in rootChildren"
          :key="child"
          :name="child"
          :parent-path="''"
          :selected-path="selectedDir"
          :depth="0"
          @select="onSelect"
        />
      </template>
    </div>

    <div v-if="showSubpath" class="mt-3">
      <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
        {{ t('admin.upload.review.extra_subpath_label') }}
      </label>
      <input
        :value="extraSubpath"
        @input="onExtraInput"
        type="text"
        :placeholder="t('admin.upload.review.extra_subpath_placeholder')"
        class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
      />
      <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ t('admin.upload.review.extra_subpath_help') }}</p>
    </div>

    <div v-if="showFinal" class="mt-3 flex items-center gap-2 flex-wrap">
      <FolderIcon class="w-4 h-4 shrink-0 text-gray-500 dark:text-gray-400" />
      <span class="text-sm font-medium text-gray-600 dark:text-gray-400">{{ t('admin.upload.review.final_location') }}</span>
      <code class="px-2 py-1 rounded bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200 font-mono text-sm font-semibold break-all">{{ finalPath }}</code>
    </div>
  </div>
</template>
