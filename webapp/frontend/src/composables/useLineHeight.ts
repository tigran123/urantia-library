import { ref, watch } from 'vue'

const KEY = 'reader-line-height'

export const LINE_HEIGHT_OPTIONS = [
  { id: '1.2', labelKey: 'app.line_spacing_tight' },
  { id: '1.5', labelKey: 'app.line_spacing_normal' },
  { id: '1.8', labelKey: 'app.line_spacing_relaxed' },
  { id: '2.0', labelKey: 'app.line_spacing_loose' },
] as const

function load(): string {
  const raw = localStorage.getItem(KEY) || ''
  return LINE_HEIGHT_OPTIONS.find(o => o.id === raw)?.id || '1.5'
}

/**
 * Shared line-height composable for text-based readers (FB2, EPUB, …).
 *
 * @param reflow – viewer-specific function that wraps a reactive mutation so the
 *                 reading position is preserved across re-pagination / re-layout.
 */
export function useLineHeight(reflow: (mutate: () => void) => void) {
  const lineHeight = ref(load())

  watch(lineHeight, (v) => {
    try { localStorage.setItem(KEY, v) } catch {}
  })

  const onLineHeightChange = (e: Event) => {
    const v = (e.target as HTMLSelectElement).value
    reflow(() => { lineHeight.value = v })
  }

  return { lineHeight, onLineHeightChange }
}
