<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { MagnifyingGlassPlusIcon, MagnifyingGlassMinusIcon, ArrowPathIcon, ArrowsPointingOutIcon, ArrowsPointingInIcon } from '@heroicons/vue/24/outline'

const { t } = useI18n({ useScope: 'global' })
defineProps<{ src: string }>()

const MIN_ZOOM = 0.1
const MAX_ZOOM = 16
const ZOOM_STEP = 1.25

const zoom = ref(1)
const tx = ref(0)
const ty = ref(0)
const isDragging = ref(false)
const immersive = ref(false)

const toggleImmersive = () => { immersive.value = !immersive.value }

watch(immersive, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
  document.documentElement.style.overflow = v ? 'hidden' : ''
})

const onKeyDown = (e: KeyboardEvent) => {
  if (e.key === 'Escape' && immersive.value) {
    e.preventDefault()
    immersive.value = false
  }
}

let dragStartX = 0
let dragStartY = 0
let dragStartTx = 0
let dragStartTy = 0

const clampZoom = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z))

// Apply a zoom factor `k` around viewport-relative point (cx, cy) measured
// from the viewport center. Keeps the image point under (cx, cy) stationary.
const applyZoom = (newZoom: number, cx = 0, cy = 0) => {
  const clamped = clampZoom(newZoom)
  const k = clamped / zoom.value
  tx.value = cx * (1 - k) + tx.value * k
  ty.value = cy * (1 - k) + ty.value * k
  zoom.value = clamped
  if (zoom.value <= 1) {
    // Snap pan back when fully zoomed out — there's nothing to pan to.
    tx.value = 0
    ty.value = 0
  }
}

const zoomIn = () => applyZoom(zoom.value * ZOOM_STEP)
const zoomOut = () => applyZoom(zoom.value / ZOOM_STEP)
const resetZoom = () => {
  zoom.value = 1
  tx.value = 0
  ty.value = 0
}

const onWheel = (e: WheelEvent) => {
  e.preventDefault()
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const cx = e.clientX - rect.left - rect.width / 2
  const cy = e.clientY - rect.top - rect.height / 2
  const factor = Math.exp(-e.deltaY * 0.0015)
  applyZoom(zoom.value * factor, cx, cy)
}

const beginDrag = (clientX: number, clientY: number) => {
  if (zoom.value <= 1) return false
  isDragging.value = true
  dragStartX = clientX
  dragStartY = clientY
  dragStartTx = tx.value
  dragStartTy = ty.value
  return true
}

const updateDrag = (clientX: number, clientY: number) => {
  tx.value = dragStartTx + (clientX - dragStartX)
  ty.value = dragStartTy + (clientY - dragStartY)
}

const endDrag = () => {
  isDragging.value = false
}

const onMouseDown = (e: MouseEvent) => {
  if (e.button !== 0) return
  if (!beginDrag(e.clientX, e.clientY)) return
  e.preventDefault()
  window.addEventListener('mousemove', onWindowMouseMove)
  window.addEventListener('mouseup', onWindowMouseUp)
}

const onWindowMouseMove = (e: MouseEvent) => {
  if (!isDragging.value) return
  updateDrag(e.clientX, e.clientY)
}

const onWindowMouseUp = () => {
  endDrag()
  window.removeEventListener('mousemove', onWindowMouseMove)
  window.removeEventListener('mouseup', onWindowMouseUp)
}

const onTouchStart = (e: TouchEvent) => {
  if (e.touches.length !== 1) return
  const t0 = e.touches[0]
  beginDrag(t0.clientX, t0.clientY)
}

const onTouchMove = (e: TouchEvent) => {
  if (!isDragging.value || e.touches.length !== 1) return
  e.preventDefault()
  const t0 = e.touches[0]
  updateDrag(t0.clientX, t0.clientY)
}

const onDoubleClick = (e: MouseEvent) => {
  if (zoom.value > 1) {
    resetZoom()
    return
  }
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const cx = e.clientX - rect.left - rect.width / 2
  const cy = e.clientY - rect.top - rect.height / 2
  applyZoom(2, cx, cy)
}

const cursorClass = computed(() => {
  if (isDragging.value) return 'cursor-grabbing'
  if (zoom.value > 1) return 'cursor-grab'
  return 'cursor-zoom-in'
})

const transform = computed(
  () => `translate(${tx.value}px, ${ty.value}px) scale(${zoom.value})`
)

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  document.body.style.overflow = ''
  document.documentElement.style.overflow = ''
  window.removeEventListener('mousemove', onWindowMouseMove)
  window.removeEventListener('mouseup', onWindowMouseUp)
})
</script>

<template>
  <div
    :class="[
      'image-viewer flex flex-col items-center bg-gray-100 dark:bg-gray-800 w-full',
      immersive
        ? 'fixed inset-0 z-50 h-dvh rounded-none'
        : 'relative rounded-lg shadow h-[80vh]'
    ]"
  >
    <!-- Toolbar (hidden in immersive) -->
    <div v-if="!immersive" class="w-full flex items-center justify-center p-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 rounded-t-lg shadow-sm gap-2 z-20">
      <button @click="zoomOut" class="p-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors" :title="t('app.zoom_out')">
        <MagnifyingGlassMinusIcon class="h-5 w-5" />
      </button>
      <span class="text-sm font-medium text-gray-700 dark:text-gray-300 w-16 text-center tabular-nums">{{ Math.round(zoom * 100) }}%</span>
      <button @click="zoomIn" class="p-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors" :title="t('app.zoom_in')">
        <MagnifyingGlassPlusIcon class="h-5 w-5" />
      </button>
      <button @click="resetZoom" class="ml-4 p-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors" :title="t('app.zoom_reset')">
        <ArrowPathIcon class="h-5 w-5" />
      </button>
      <button @click="toggleImmersive" class="ml-4 p-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-800 dark:text-gray-200 rounded transition-colors" :title="t('app.immersive_enter')">
        <ArrowsPointingOutIcon class="h-5 w-5" />
      </button>
    </div>

    <!-- Image Container -->
    <div
      class="relative w-full flex-grow overflow-hidden bg-gray-200 dark:bg-gray-900 select-none touch-none"
      :class="[cursorClass, immersive ? '' : 'rounded-b-lg']"
      @wheel="onWheel"
      @mousedown="onMouseDown"
      @dblclick="onDoubleClick"
      @touchstart.passive="onTouchStart"
      @touchmove="onTouchMove"
      @touchend="endDrag"
      @touchcancel="endDrag"
    >
      <div
        class="absolute inset-0 flex items-center justify-center will-change-transform"
        :class="{ 'transition-transform duration-150 ease-out': !isDragging }"
        :style="{ transform, transformOrigin: 'center center' }"
      >
        <img
          :src="src"
          class="max-w-full max-h-full object-contain pointer-events-none select-none"
          draggable="false"
        />
      </div>
    </div>

    <button
      v-if="immersive"
      @click="toggleImmersive"
      :title="t('app.immersive_exit')"
      class="absolute top-2 right-2 z-40 p-2 rounded-full bg-black/15 hover:bg-black/40 text-white/80 hover:text-white"
    >
      <ArrowsPointingInIcon class="h-5 w-5" />
    </button>
  </div>
</template>
