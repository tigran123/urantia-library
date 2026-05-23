import { ref, watch, onBeforeUnmount, nextTick, type Ref } from 'vue'
import type { Annotation } from '../api'
import { anchorFromSelection, paintAnnotations } from '../lib/anchors/html'
import { popoverPosition, type PopoverPosition } from '../lib/anchors/popoverPosition'
import type { useAnnotations } from './useAnnotations'

const sameHtmlAnchor = (a: any, b: any): boolean =>
  a?.type === 'html' && b?.type === 'html'
  && a.containerAnchor === b.containerAnchor
  && a.startOffset === b.startOffset
  && a.endOffset === b.endOffset
  && (a.endContainerAnchor ?? null) === (b.endContainerAnchor ?? null)

type AnnotationsApi = ReturnType<typeof useAnnotations>

interface Pending {
  selectedText: string
  prefix: string
  suffix: string
  anchor: { type: 'html'; containerAnchor: number; startOffset: number; endOffset: number; endContainerAnchor?: number }
}

/* Shared wiring for HTML / Markdown / FB2 viewers. The host provides the
   scroll container (used to position the popover) and the content container
   under which the rendered HTML lives (via `v-html`). Selection capture,
   click-to-edit, and re-paint on visibility/annotation changes all live here. */
export function useHtmlAnnotationLayer(
  scrollEl: Ref<HTMLElement | null>,
  contentEl: Ref<HTMLElement | null>,
  ann: AnnotationsApi,
) {
  const pending = ref<Pending | null>(null)
  const existingId = ref<number | null>(null)
  const existing = ref<Annotation | null>(null)
  const position = ref<PopoverPosition | null>(null)

  const closePopover = () => {
    pending.value = null
    existingId.value = null
    existing.value = null
    position.value = null
  }

  const repaint = () => {
    const el = contentEl.value
    if (!el) return
    paintAnnotations(el, ann.visible.value, {
      onClick: (id, ev) => {
        const found = ann.annotations.value.find(a => a.id === id) || null
        existing.value = found
        existingId.value = id
        pending.value = null
        // Anchor the popover beside the clicked mark.
        const mark = (ev.target as HTMLElement | null)?.closest?.('mark.anno') as HTMLElement | null
        const scroll = scrollEl.value
        if (mark && scroll) {
          position.value = popoverPosition(mark.getBoundingClientRect(), scroll)
        }
      },
    })
  }

  // Wait one tick after v-html mounts before painting, so the DOM is ready.
  const repaintSoon = async () => {
    await nextTick()
    repaint()
  }

  watch(ann.visible, repaintSoon, { deep: false })
  watch(ann.annotations, repaintSoon, { deep: false })

  // Capture the current selection (if any) and either open the create-mode
  // popover for it or — when it exactly matches an annotation the current
  // user already owns — open that annotation in edit mode. The latter case
  // prevents the "I made a second annotation on the same word and now my
  // first one looks overwritten" bug: when two annotations share the same
  // anchor, paintAnnotations nests them so only the inner mark is clickable,
  // making the older one effectively unreachable.
  const tryShowPopoverFromSelection = () => {
    const content = contentEl.value
    if (!content) return
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return
    const range = sel.getRangeAt(0)
    if (!content.contains(range.startContainer) || !content.contains(range.endContainer)) return
    // Ignore selections inside our own popover (e.g. dragging in the note text area).
    if ((range.startContainer.parentElement || range.startContainer as HTMLElement | null)?.closest?.('.anno-popover')) return

    const anchored = anchorFromSelection(content, range)
    if (!anchored) return

    const scroll = scrollEl.value
    if (!scroll) return
    position.value = popoverPosition(range.getBoundingClientRect(), scroll)

    // If we already own an annotation with the same anchor, open that one for
    // editing instead of creating a new (and unreachable) duplicate.
    const dup = ann.annotations.value.find(a =>
      a.is_own && a.selected_text === anchored.selectedText && sameHtmlAnchor(a.anchor, anchored.anchor),
    )
    if (dup) {
      existing.value = dup
      existingId.value = dup.id
      pending.value = null
      return
    }

    pending.value = {
      selectedText: anchored.selectedText,
      prefix: anchored.prefix,
      suffix: anchored.suffix,
      anchor: anchored.anchor,
    }
    existing.value = null
    existingId.value = null
  }

  const onSelectionEnd = (e: MouseEvent) => {
    // Ignore clicks inside the popover itself or on existing marks (the click
    // handler on marks takes priority).
    const target = e.target as HTMLElement | null
    if (target?.closest('.anno-popover')) return
    if (target?.closest('mark.anno')) return

    const sel = window.getSelection()
    if (!sel || sel.isCollapsed) {
      if (!existing.value) closePopover()
      return
    }
    tryShowPopoverFromSelection()
  }

  // selectionchange catches the cases mouseup misses: touch-driven selection
  // on Android, dragging the selection handles, and IME-driven changes. We
  // debounce so the popover only appears once the user has stopped adjusting.
  let selDebounce: number | null = null
  const onSelectionChange = () => {
    if (selDebounce !== null) window.clearTimeout(selDebounce)
    selDebounce = window.setTimeout(() => {
      selDebounce = null
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) return
      const content = contentEl.value
      if (!content) return
      const range = sel.getRangeAt(0)
      if (!content.contains(range.startContainer) || !content.contains(range.endContainer)) return
      tryShowPopoverFromSelection()
    }, 350)
  }
  document.addEventListener('selectionchange', onSelectionChange)
  onBeforeUnmount(() => {
    if (selDebounce !== null) window.clearTimeout(selDebounce)
    document.removeEventListener('selectionchange', onSelectionChange)
  })

  const onSaveCreate = async (payload: { body: string | null; isPublic: boolean }) => {
    if (!pending.value) return
    try {
      await ann.create(pending.value.anchor, pending.value.selectedText, {
        body: payload.body,
        isPublic: payload.isPublic,
        prefix: pending.value.prefix,
        suffix: pending.value.suffix,
      })
      window.getSelection()?.removeAllRanges()
      closePopover()
    } catch (e: any) {
      console.error('Failed to save annotation', e)
      alert(e.response?.data?.detail || e.message || 'Failed to save annotation')
    }
  }

  const onUpdate = async (payload: { id: number; body: string | null; isPublic: boolean }) => {
    try {
      await ann.update(payload.id, { body: payload.body, is_public: payload.isPublic })
      closePopover()
    } catch (e: any) {
      console.error('Failed to update annotation', e)
      alert(e.response?.data?.detail || e.message || 'Failed to update annotation')
    }
  }

  const onDelete = async (id: number) => {
    try {
      await ann.remove(id)
      closePopover()
    } catch (e: any) {
      console.error('Failed to delete annotation', e)
      alert(e.response?.data?.detail || e.message || 'Failed to delete annotation')
    }
  }

  const jumpTo = (a: Annotation) => {
    const content = contentEl.value
    if (!content) return
    const mark = content.querySelector<HTMLElement>(`mark.anno[data-annotation-id="${a.id}"]`)
    const scroll = scrollEl.value
    if (mark && scroll) {
      const r = mark.getBoundingClientRect()
      const sr = scroll.getBoundingClientRect()
      scroll.scrollTop += r.top - sr.top - 40
      // Re-measure after scroll so the popover anchor uses the post-scroll rect.
      nextTick().then(() => {
        if (!scroll) return
        const after = mark.getBoundingClientRect()
        position.value = popoverPosition(after, scroll)
      })
    }
    existing.value = a
    existingId.value = a.id
    pending.value = null
  }

  // Close on Escape so the user always has a way out without clicking.
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && (pending.value || existing.value)) {
      closePopover()
    }
  }
  window.addEventListener('keydown', onKey)
  onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

  return {
    pending,
    existing,
    position,
    closePopover,
    repaint,
    repaintSoon,
    onSelectionEnd,
    onSaveCreate,
    onUpdate,
    onDelete,
    jumpTo,
  }
}
