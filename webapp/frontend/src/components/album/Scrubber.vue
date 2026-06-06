<script setup lang="ts">
// Thin draggable progress bar — click *and* drag via Pointer Events with
// setPointerCapture, so the pointer keeps reporting even if it leaves the bar.
// Shared by the seek bar and the volume slider in the now-playing bar.
import { ref, computed } from 'vue'

const props = defineProps<{ value: number; max: number }>()
const emit = defineEmits<{ (e: 'seek', value: number): void }>()

const track = ref<HTMLElement | null>(null)
const dragging = ref(false)

const pct = computed(() => (props.max ? Math.min(100, Math.max(0, (props.value / props.max) * 100)) : 0))

function valueFromEvent(e: PointerEvent): number | null {
  const el = track.value
  if (!el) return null
  const rect = el.getBoundingClientRect()
  if (rect.width === 0) return null
  return Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * props.max
}

function onDown(e: PointerEvent) {
  e.preventDefault()
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  const v = valueFromEvent(e)
  if (v != null) emit('seek', v)
}
function onMove(e: PointerEvent) {
  if (!dragging.value) return
  const v = valueFromEvent(e)
  if (v != null) emit('seek', v)
}
function end() { dragging.value = false }
</script>

<template>
  <div
    ref="track"
    @pointerdown="onDown"
    @pointermove="onMove"
    @pointerup="end"
    @pointercancel="end"
    class="group/scrub relative h-1.5 rounded-full bg-gray-200 dark:bg-gray-600 cursor-pointer touch-none select-none"
  >
    <div
      class="absolute inset-y-0 left-0 rounded-full"
      :class="dragging ? 'bg-blue-600' : 'bg-blue-500 group-hover/scrub:bg-blue-600'"
      :style="{ width: pct + '%' }"
    />
    <div
      class="absolute top-1/2 -translate-y-1/2 h-3 w-3 rounded-full bg-blue-600 shadow transition-opacity"
      :class="dragging ? 'opacity-100' : 'opacity-0 group-hover/scrub:opacity-100'"
      :style="{ left: `calc(${pct}% - 6px)` }"
    />
  </div>
</template>
