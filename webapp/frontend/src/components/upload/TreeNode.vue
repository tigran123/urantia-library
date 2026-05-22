<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChevronRightIcon, ChevronDownIcon, FolderIcon } from '@heroicons/vue/24/outline'
import api from '../../api'

const props = defineProps<{
  name: string
  parentPath: string
  selectedPath: string
  depth: number
}>()

const emit = defineEmits<{ (e: 'select', path: string): void }>()

const fullPath = computed(() =>
  props.parentPath ? `${props.parentPath}/${props.name}` : props.name
)
const isSelected = computed(() => props.selectedPath === fullPath.value)

const expanded = ref(false)
const loaded = ref(false)
const loading = ref(false)
const children = ref<string[]>([])
const error = ref('')

const toggle = async () => {
  if (expanded.value) {
    expanded.value = false
    return
  }
  expanded.value = true
  if (loaded.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.get('/admin/dirs', { params: { path: fullPath.value } })
    children.value = res.data?.dirs || []
    loaded.value = true
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || 'Failed'
  } finally {
    loading.value = false
  }
}

const onSelect = () => emit('select', fullPath.value)
const onChildSelect = (path: string) => emit('select', path)
</script>

<template>
  <div>
    <div
      class="flex items-center gap-1 px-1 py-0.5 rounded"
      :class="isSelected ? 'bg-blue-100 dark:bg-blue-900/40' : 'hover:bg-gray-100 dark:hover:bg-gray-800'"
      :style="{ paddingLeft: `${depth * 16 + 4}px` }"
    >
      <button
        type="button"
        @click="toggle"
        class="p-0.5 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 rounded"
        :aria-label="expanded ? 'Collapse' : 'Expand'"
      >
        <ChevronDownIcon v-if="expanded" class="w-3.5 h-3.5" />
        <ChevronRightIcon v-else class="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        @click="onSelect"
        class="flex-1 flex items-center gap-1.5 px-1 py-0.5 text-left font-mono text-sm"
        :class="isSelected
          ? 'text-blue-800 dark:text-blue-200'
          : 'text-gray-800 dark:text-gray-200'"
      >
        <FolderIcon class="w-4 h-4 shrink-0 text-gray-500 dark:text-gray-400" />
        <span>{{ name }}</span>
      </button>
    </div>
    <div v-if="expanded">
      <div
        v-if="loading"
        :style="{ paddingLeft: `${(depth + 1) * 16 + 24}px` }"
        class="text-xs text-gray-500 dark:text-gray-400 py-0.5"
      >…</div>
      <div
        v-else-if="error"
        :style="{ paddingLeft: `${(depth + 1) * 16 + 24}px` }"
        class="text-xs text-red-600 dark:text-red-400 py-0.5"
      >{{ error }}</div>
      <div
        v-else-if="loaded && !children.length"
        :style="{ paddingLeft: `${(depth + 1) * 16 + 24}px` }"
        class="text-xs text-gray-400 dark:text-gray-500 py-0.5 italic"
      >∅</div>
      <TreeNode
        v-else
        v-for="child in children"
        :key="child"
        :name="child"
        :parent-path="fullPath"
        :selected-path="selectedPath"
        :depth="depth + 1"
        @select="onChildSelect"
      />
    </div>
  </div>
</template>
