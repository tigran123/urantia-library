import { ref, watch } from 'vue'

export type GridItemSize = 'small' | 'normal' | 'large'

const KEY = 'gridItemSize'
const VALID: GridItemSize[] = ['small', 'normal', 'large']

const stored = localStorage.getItem(KEY)
export const gridItemSize = ref<GridItemSize>(
  (VALID as string[]).includes(stored ?? '') ? (stored as GridItemSize) : 'normal'
)

watch(gridItemSize, (v) => {
  localStorage.setItem(KEY, v)
})

export const GRID_CLASSES: Record<GridItemSize, string> = {
  small:  'gap-2 sm:gap-3 lg:gap-3 grid-cols-[repeat(auto-fill,minmax(100px,1fr))]',
  normal: 'gap-3 sm:gap-4 lg:gap-6 grid-cols-[repeat(auto-fill,minmax(140px,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(130px,1fr))] lg:grid-cols-[repeat(auto-fill,minmax(180px,1fr))]',
  large:  'gap-4 sm:gap-6 lg:gap-8 grid-cols-[repeat(auto-fill,minmax(160px,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] lg:grid-cols-[repeat(auto-fill,minmax(220px,1fr))]',
}
