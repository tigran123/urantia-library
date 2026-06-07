import { ref, watch } from 'vue'

export type TextSize = 'small' | 'normal' | 'large' | 'xlarge'

const KEY = 'textSize'
const VALID: TextSize[] = ['small', 'normal', 'large', 'xlarge']

// Root font-size applied to <html>. Because Tailwind text/spacing utilities are
// rem-based, this scales the whole app UI from one knob. Cover thumbnails use px
// column widths, so the grid layout doesn't blow up. The in-app document readers
// are decoupled via the fixed `--reader-base` (see style.css) so this never
// compounds with their own A+/A- controls.
export const TEXT_SIZE_PX: Record<TextSize, string> = {
  small:  '14px',
  normal: '16px',
  large:  '18px',
  xlarge: '20px',
}

const stored = localStorage.getItem(KEY)
export const textSize = ref<TextSize>(
  (VALID as string[]).includes(stored ?? '') ? (stored as TextSize) : 'normal'
)

function apply(v: TextSize) {
  document.documentElement.style.fontSize = TEXT_SIZE_PX[v]
}

// Apply the saved value at module load, before the app mounts (main.ts imports
// this module for its side effect), mirroring how the dark-mode class is set.
apply(textSize.value)

watch(textSize, (v) => {
  localStorage.setItem(KEY, v)
  apply(v)
})
