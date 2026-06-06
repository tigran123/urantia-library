<script setup lang="ts">
// Single-line text that scrolls (marquee, right-to-left and back) when it
// overflows its container — used for the title + italic composer on desktop
// rows so the composer is reachable without its own column. It animates only
// when overflowing AND the row is current or hovered; otherwise it sits clipped
// at the start. Measurement uses an always-inline-block inner so toggling the
// animation never changes layout size (which would otherwise oscillate).
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'

const props = defineProps<{ active?: boolean }>()

const container = ref<HTMLElement | null>(null)
const inner = ref<HTMLElement | null>(null)
const overflow = ref(0) // px the content exceeds the container
const hovered = ref(false)

const measure = () => {
  const c = container.value
  const i = inner.value
  if (!c || !i) return
  overflow.value = Math.max(0, i.scrollWidth - c.clientWidth)
}

// Touch devices have no hover to reveal the clipped text, so overflowing lines
// scroll unconditionally there; on hover-capable devices we stay calm until the
// row is current or the pointer is over the text.
const noHover = typeof window !== 'undefined' && !!window.matchMedia?.('(hover: none)').matches
const scrolling = computed(() => overflow.value > 0 && (props.active || hovered.value || noHover))
const styleVars = computed(() => ({
  '--marquee-shift': `-${overflow.value}px`,
  // ~24px/sec with a floor, so short overflows still drift gently.
  '--marquee-duration': `${Math.max(5, overflow.value / 24)}s`,
}))

let ro: ResizeObserver | null = null
onMounted(() => {
  nextTick(measure)
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => measure())
    if (container.value) ro.observe(container.value)
  }
})
onBeforeUnmount(() => ro?.disconnect())
watch(() => props.active, () => nextTick(measure))
</script>

<template>
  <div
    ref="container"
    class="overflow-hidden"
    @mouseenter="hovered = true"
    @mouseleave="hovered = false"
  >
    <div
      ref="inner"
      class="inline-block whitespace-nowrap align-bottom"
      :class="{ 'marquee-run': scrolling }"
      :style="scrolling ? styleVars : undefined"
    >
      <slot />
    </div>
  </div>
</template>

<style scoped>
.marquee-run {
  animation: marquee var(--marquee-duration, 8s) ease-in-out infinite;
}
@keyframes marquee {
  0%, 12% { transform: translateX(0); }
  45%, 55% { transform: translateX(var(--marquee-shift, 0)); }
  88%, 100% { transform: translateX(0); }
}
</style>
