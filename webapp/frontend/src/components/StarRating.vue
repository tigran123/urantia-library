<script setup lang="ts">
import { ref, computed } from 'vue'
import { StarIcon as StarSolid } from '@heroicons/vue/24/solid'
import { StarIcon as StarOutline } from '@heroicons/vue/24/outline'

/**
 * Star rating display / input.
 * - Read-only mode (default) supports fractional `rating` with partial fill,
 *   used to show a book's average rating.
 * - `interactive` mode lets the user click a star (1..5); emits `update:rating`.
 */
const props = withDefaults(defineProps<{
  rating: number | null
  count?: number | null
  interactive?: boolean
  size?: string        // tailwind sizing class, e.g. 'h-4 w-4'
  showCount?: boolean
}>(), {
  count: null,
  interactive: false,
  size: 'h-4 w-4',
  showCount: true,
})

const emit = defineEmits<{ (e: 'update:rating', value: number): void }>()

const hover = ref(0)

const displayValue = computed(() => {
  if (props.interactive) return hover.value || props.rating || 0
  return props.rating || 0
})

// Percentage of star `i` (1..5) that should be filled.
function fillPct(i: number): number {
  const v = displayValue.value - (i - 1)
  if (v >= 1) return 100
  if (v <= 0) return 0
  return Math.round(v * 100)
}

function pick(i: number) {
  if (props.interactive) emit('update:rating', i)
}
</script>

<template>
  <span class="inline-flex items-center gap-1 align-middle">
    <span class="inline-flex" :class="interactive ? 'cursor-pointer' : ''">
      <span
        v-for="i in 5"
        :key="i"
        class="relative inline-block leading-none"
        @click="pick(i)"
        @mouseenter="interactive && (hover = i)"
        @mouseleave="interactive && (hover = 0)"
      >
        <StarOutline :class="[size, 'text-yellow-400']" />
        <span
          class="absolute inset-0 overflow-hidden"
          :style="{ width: fillPct(i) + '%' }"
        >
          <StarSolid :class="[size, 'text-yellow-400']" />
        </span>
      </span>
    </span>
    <span
      v-if="showCount && rating != null"
      class="text-xs text-gray-500 dark:text-gray-400"
    >
      {{ rating.toFixed(1) }}<template v-if="count"> ({{ count }})</template>
    </span>
  </span>
</template>
