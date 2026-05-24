import { ref, watch, computed } from 'vue'

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

export const gridCls = computed(() => {
  const small = gridItemSize.value === 'small'
  return {
    card: small ? 'p-2' : 'p-4',
    coverMargin: small ? 'mb-2' : 'mb-3',
    iconBtn: small ? 'p-1' : 'p-1.5',
    icon: small ? 'h-3.5 w-3.5' : 'h-5 w-5',
    badge: small ? 'px-1 text-[10px]' : 'px-1.5 py-0.5 text-xs',
    title: small ? 'text-xs' : 'text-sm',
    subtitle: small ? 'text-[11px]' : 'text-xs',
    bigIcon: small ? 'h-10 w-10' : 'h-16 w-16',
  }
})

// Mirrors the minColWidth / gap encoded in GRID_CLASSES, breakpoints at
// Tailwind defaults (sm=640, lg=1024). Used by paginators that want to round
// per_page to a multiple of columns so the last row is never half-empty.
export function estimateGridCols(width: number, size: GridItemSize): number {
  const ge = (n: number) => width >= n
  let minCol: number
  let gap: number
  if (size === 'small') {
    minCol = 100
    gap = ge(1024) ? 12 : ge(640) ? 12 : 8
  } else if (size === 'large') {
    minCol = ge(1024) ? 220 : ge(640) ? 180 : 160
    gap = ge(1024) ? 32 : ge(640) ? 24 : 16
  } else {
    minCol = ge(1024) ? 180 : ge(640) ? 130 : 140
    gap = ge(1024) ? 24 : ge(640) ? 16 : 12
  }
  // ~16px page padding on each side of the SPA main column at typical layouts.
  const usable = Math.max(0, width - 32)
  return Math.max(1, Math.floor((usable + gap) / (minCol + gap)))
}

// Round target per_page to the nearest multiple of cols, always ≥ cols so we
// never request fewer than one full row.
export function roundToRowMultiple(target: number, cols: number): number {
  if (cols <= 1) return target
  return Math.max(cols, Math.round(target / cols) * cols)
}
