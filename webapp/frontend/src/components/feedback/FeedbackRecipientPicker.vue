<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { UsersIcon, PlusCircleIcon, XMarkIcon, CheckIcon } from '@heroicons/vue/24/outline'
import type { AdminBrief } from '../../api'

const props = defineProps<{
  admins: AdminBrief[]
  selectedIds: number[]
  currentUserId?: number
}>()
const emit = defineEmits<{ (e: 'update:selectedIds', ids: number[]): void }>()

const { t } = useI18n()
const open = ref(false)

const broadcast = computed(() => props.selectedIds.length === 0)
const selected = computed(() =>
  props.admins.filter(a => props.selectedIds.includes(a.id)),
)

function initials(name: string) {
  return name.split(/\s+/).map(s => s[0] ?? '').join('').slice(0, 2).toUpperCase()
}

function toggle(id: number) {
  const picked = props.selectedIds.includes(id)
  const next = picked
    ? props.selectedIds.filter(i => i !== id)
    : [...props.selectedIds, id]
  emit('update:selectedIds', next)
}

function clearAll() {
  emit('update:selectedIds', [])
  open.value = false
}
</script>

<template>
  <div class="relative">
    <div class="flex items-center flex-wrap gap-1.5">
      <span
        v-if="broadcast"
        class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 text-blue-800 dark:text-blue-200 text-xs font-medium"
      >
        <UsersIcon class="h-3.5 w-3.5" />
        {{ t('feedback.all_admins') }}
      </span>
      <template v-else>
        <span
          v-for="a in selected"
          :key="a.id"
          class="inline-flex items-center gap-1.5 pr-1.5 pl-1 py-1 rounded-full bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-800 dark:text-gray-100 text-xs"
        >
          <span class="h-5 w-5 rounded-full bg-blue-600 text-white text-[9px] font-bold flex items-center justify-center">
            {{ initials(a.name) }}
          </span>
          <span class="truncate max-w-[10rem]">
            {{ a.name }}<span v-if="a.id === currentUserId" class="text-gray-400 dark:text-gray-500 font-normal"> {{ t('feedback.you') }}</span>
          </span>
          <button
            type="button"
            class="ml-0.5 opacity-50 hover:opacity-100"
            @click="toggle(a.id)"
          >
            <XMarkIcon class="h-3 w-3" />
          </button>
        </span>
      </template>
      <button
        type="button"
        class="inline-flex items-center gap-1 px-2 py-1 text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 hover:underline"
        @click="open = !open"
      >
        <PlusCircleIcon class="h-3.5 w-3.5" />
        {{ broadcast ? t('feedback.pick_specific') : t('feedback.add_admin') }}
      </button>
    </div>

    <div v-if="open" class="fixed inset-0 z-30" @click="open = false" />

    <div
      v-if="open"
      class="absolute z-40 mt-2 w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-md shadow-lg overflow-hidden"
    >
      <button
        type="button"
        class="w-full flex items-center gap-2 px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
        :class="broadcast
          ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-800 dark:text-blue-200 font-medium'
          : 'text-gray-700 dark:text-gray-200'"
        @click="clearAll"
      >
        <UsersIcon class="h-4 w-4" />
        <span>{{ t('feedback.all_admins') }}</span>
        <CheckIcon v-if="broadcast" class="h-4 w-4 ml-auto" />
      </button>
      <div class="border-t border-gray-100 dark:border-gray-700">
        <button
          v-for="a in admins"
          :key="a.id"
          type="button"
          class="w-full flex items-center gap-2 px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200"
          @click="toggle(a.id)"
        >
          <span class="h-6 w-6 rounded-full bg-blue-600 text-white text-[10px] font-bold flex items-center justify-center">
            {{ initials(a.name) }}
          </span>
          <span class="flex-1 min-w-0">
            <span class="block truncate">
              {{ a.name }}<span v-if="a.id === currentUserId" class="text-gray-400 dark:text-gray-500"> {{ t('feedback.you') }}</span>
            </span>
            <span class="block text-[11px] text-gray-500 dark:text-gray-400 font-mono truncate">{{ a.email }}</span>
          </span>
          <CheckIcon
            v-if="selectedIds.includes(a.id)"
            class="h-4 w-4 text-blue-600 dark:text-blue-400"
          />
        </button>
      </div>
    </div>
  </div>
</template>
