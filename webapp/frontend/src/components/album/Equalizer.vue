<script setup lang="ts">
// Animated 4-bar equalizer shown in place of the track number on the current
// row. Bars animate when playing; frozen at 35% height when paused (we drop the
// animation entirely so the inline transform takes effect).
defineProps<{ playing?: boolean }>()
</script>

<template>
  <span class="inline-flex items-end gap-[2px] h-4 text-blue-600 dark:text-blue-400" aria-hidden="true">
    <span
      v-for="i in 4"
      :key="i"
      class="eqbar w-[2.5px] rounded-full bg-current"
      :class="playing ? 'eqbar--anim' : ''"
      :style="playing ? { animationDelay: (i - 1) * 140 + 'ms' } : { transform: 'scaleY(0.35)' }"
    />
  </span>
</template>

<style scoped>
.eqbar {
  height: 100%;
  transform-origin: bottom;
}
.eqbar--anim {
  animation: eqbar 900ms ease-in-out infinite;
}
@keyframes eqbar {
  0%, 100% { transform: scaleY(0.35); }
  50% { transform: scaleY(1); }
}
</style>
