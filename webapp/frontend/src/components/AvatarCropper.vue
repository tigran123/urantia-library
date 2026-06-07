<script setup lang="ts">
// Hand-rolled avatar cropper: a fixed square viewport with a circular crop
// guide, drag-to-reposition, and zoom (slider + wheel). On apply it renders the
// selected region to a square canvas and emits a JPEG Blob. No dependency — the
// pan gesture mirrors Scrubber.vue's Pointer Events + setPointerCapture pattern,
// and canvas.toBlob works fine over plain HTTP (not a secure-context API).
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { MinusIcon, PlusIcon } from '@heroicons/vue/24/outline'

const props = defineProps<{ file: File }>()
const emit = defineEmits<{ (e: 'crop', blob: Blob): void; (e: 'cancel'): void }>()

const { t } = useI18n({ useScope: 'global' })

const V = 256        // viewport (CSS px)
const OUT = 512      // exported avatar size (px)
const ZMAX = 3       // max zoom (× the cover scale)

const imgEl = ref<HTMLImageElement | null>(null)
const objectUrl = ref('')
const naturalW = ref(0)
const naturalH = ref(0)
const zoom = ref(1)
const offsetX = ref(0)   // image top-left relative to viewport top-left (display px)
const offsetY = ref(0)
const loaded = ref(false)

// Min scale that still covers the square viewport; zoom multiplies from there.
const coverScale = computed(() =>
  naturalW.value && naturalH.value ? Math.max(V / naturalW.value, V / naturalH.value) : 1,
)
const displayScale = computed(() => coverScale.value * zoom.value)
const imgW = computed(() => naturalW.value * displayScale.value)
const imgH = computed(() => naturalH.value * displayScale.value)

const imgStyle = computed(() => ({
  width: `${imgW.value}px`,
  height: `${imgH.value}px`,
  transform: `translate(${offsetX.value}px, ${offsetY.value}px)`,
}))

// Keep the image covering the viewport: offset stays within [V − imgSize, 0].
function clamp() {
  offsetX.value = Math.max(V - imgW.value, Math.min(0, offsetX.value))
  offsetY.value = Math.max(V - imgH.value, Math.min(0, offsetY.value))
}

function onImageLoad() {
  const img = imgEl.value
  if (!img) return
  naturalW.value = img.naturalWidth
  naturalH.value = img.naturalHeight
  zoom.value = 1
  // Center the (cover-scaled) image in the viewport.
  offsetX.value = (V - imgW.value) / 2
  offsetY.value = (V - imgH.value) / 2
  clamp()
  loaded.value = true
}

// Zoom about the viewport centre so the focal point stays put, then re-clamp.
function applyZoom(next: number) {
  next = Math.max(1, Math.min(ZMAX, next))
  const oldScale = displayScale.value
  const newScale = coverScale.value * next
  const cx = (V / 2 - offsetX.value) / oldScale
  const cy = (V / 2 - offsetY.value) / oldScale
  zoom.value = next
  offsetX.value = V / 2 - cx * newScale
  offsetY.value = V / 2 - cy * newScale
  clamp()
}

const onSlider = (e: Event) => applyZoom(parseFloat((e.target as HTMLInputElement).value))
const onWheel = (e: WheelEvent) => applyZoom(zoom.value * (e.deltaY < 0 ? 1.1 : 1 / 1.1))

// --- pan (Pointer Events, like Scrubber.vue) ---
const dragging = ref(false)
let startX = 0, startY = 0, startOX = 0, startOY = 0
function onDown(e: PointerEvent) {
  if (!loaded.value) return
  e.preventDefault()
  dragging.value = true
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  startX = e.clientX; startY = e.clientY
  startOX = offsetX.value; startOY = offsetY.value
}
function onMove(e: PointerEvent) {
  if (!dragging.value) return
  offsetX.value = startOX + (e.clientX - startX)
  offsetY.value = startOY + (e.clientY - startY)
  clamp()
}
function end() { dragging.value = false }

function apply() {
  const img = imgEl.value
  if (!img || !naturalW.value) return
  const scale = displayScale.value
  // Source rect (natural px) currently shown in the viewport.
  const sz = Math.min(V / scale, naturalW.value, naturalH.value)
  const sx = Math.max(0, Math.min(-offsetX.value / scale, naturalW.value - sz))
  const sy = Math.max(0, Math.min(-offsetY.value / scale, naturalH.value - sz))
  const canvas = document.createElement('canvas')
  canvas.width = OUT; canvas.height = OUT
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.fillStyle = '#fff'  // flatten any transparency (the corners are hidden by the circle anyway)
  ctx.fillRect(0, 0, OUT, OUT)
  ctx.drawImage(img, sx, sy, sz, sz, 0, 0, OUT, OUT)
  canvas.toBlob((blob) => { if (blob) emit('crop', blob) }, 'image/jpeg', 0.9)
}

// (Re)load when the chosen file changes; revoke the previous object URL.
watch(() => props.file, (f) => {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
  loaded.value = false
  objectUrl.value = f ? URL.createObjectURL(f) : ''
}, { immediate: true })

onBeforeUnmount(() => { if (objectUrl.value) URL.revokeObjectURL(objectUrl.value) })
</script>

<template>
  <div class="flex flex-col items-center gap-4">
    <!-- viewport: drag to pan, scroll to zoom; circular guide dims the corners -->
    <div
      class="relative overflow-hidden bg-gray-100 dark:bg-gray-900 rounded-lg select-none touch-none"
      :class="dragging ? 'cursor-grabbing' : 'cursor-grab'"
      :style="{ width: V + 'px', height: V + 'px' }"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="end"
      @pointercancel="end"
      @wheel.prevent="onWheel"
    >
      <img
        ref="imgEl"
        :src="objectUrl"
        @load="onImageLoad"
        :style="imgStyle"
        class="absolute top-0 left-0 max-w-none pointer-events-none"
        draggable="false"
        alt=""
      />
      <!-- circular crop guide: the big spread shadow (clipped to the viewport)
           dims everything outside the circle -->
      <div
        class="absolute inset-0 rounded-full ring-2 ring-white/80 pointer-events-none"
        style="box-shadow: 0 0 0 9999px rgba(0,0,0,0.45)"
      />
    </div>

    <!-- zoom control -->
    <div class="flex items-center gap-2 w-64">
      <MinusIcon class="h-4 w-4 shrink-0 text-gray-400" />
      <input
        type="range" min="1" :max="ZMAX" step="0.01" :value="zoom" @input="onSlider"
        :aria-label="t('settings.crop_zoom')"
        class="flex-1 accent-blue-600 cursor-pointer"
      />
      <PlusIcon class="h-4 w-4 shrink-0 text-gray-400" />
    </div>

    <p class="text-xs text-gray-500 dark:text-gray-400 text-center">{{ t('settings.crop_hint') }}</p>

    <div class="flex items-center justify-end gap-3 w-full">
      <button
        type="button"
        @click="emit('cancel')"
        class="px-4 py-2 rounded-md text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
      >
        {{ t('settings.crop_cancel') }}
      </button>
      <button
        type="button"
        @click="apply"
        :disabled="!loaded"
        class="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded hover:bg-blue-700 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {{ t('settings.crop_apply') }}
      </button>
    </div>
  </div>
</template>
