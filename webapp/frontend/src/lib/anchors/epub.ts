// Anchor model for the EPUB viewer. ePub.js gives us its own canonical
// fragment identifier (CFI) that is reflow-stable: the same CFI points to
// the same content even after font-size or pagination changes. Selection is
// just `rendition.getRange(cfiRange).toString()` and CFI ranges look like:
//   `epubcfi(/6/4[chap01]!/4/10/2,/1:0,/1:42)`

import type { Rendition } from 'epubjs'
import type { Annotation, AnnotationAnchor } from '../../api'

const CONTEXT_LEN = 64

export interface EpubAnchor {
  type: 'epub'
  cfiRange: string
}

export interface AnchoredSelection {
  anchor: EpubAnchor
  selectedText: string
  prefix: string
  suffix: string
}

// Inside an iframe-rendered EPUB section, derive selection context (the text
// before and after the selected range) by walking the section's textContent.
const contextFromRange = (range: Range): { prefix: string; suffix: string } => {
  const root = range.commonAncestorContainer.ownerDocument?.body
    || (range.commonAncestorContainer as Node)
  const full = root.textContent ?? ''
  const selected = range.toString()
  const idx = full.indexOf(selected)
  if (idx < 0) return { prefix: '', suffix: '' }
  return {
    prefix: full.slice(Math.max(0, idx - CONTEXT_LEN), idx),
    suffix: full.slice(idx + selected.length, idx + selected.length + CONTEXT_LEN),
  }
}

export const anchorFromSelection = (cfiRange: string, range: Range): AnchoredSelection | null => {
  const selectedText = range.toString()
  if (!selectedText) return null
  const { prefix, suffix } = contextFromRange(range)
  return {
    anchor: { type: 'epub', cfiRange },
    selectedText,
    prefix,
    suffix,
  }
}

export interface PaintOptions {
  rendition: Rendition
  onClick?: (annotationId: number) => void
}

// Track the CFI ranges we have currently injected, per rendition store, so we
// can wipe just our own overlays on re-paint. ePub.js exposes
// `.annotations.each(cb)` but its implementation calls `forEach` on a plain
// object, which throws — so we keep our own list.
const ownedByStore = new WeakMap<object, string[]>()

const ownedFor = (annotationsStore: object): string[] => {
  let list = ownedByStore.get(annotationsStore)
  if (!list) { list = []; ownedByStore.set(annotationsStore, list) }
  return list
}

// ePub.js renders annotations as SVG <g> elements via marks-pane. The fill
// goes on the group; child <rect>s inherit it. We pass solid colors plus a
// separate fill-opacity (instead of an rgba() with alpha) because some
// browsers don't apply alpha consistently from rgba on SVG fill, while
// fill-opacity is rock-solid.
const FILL_MINE  = '#facc15'   // yellow-400
const FILL_OTHER = '#f97316'   // orange-500
const FILL_OPACITY = '0.45'

export const paintAnnotations = (
  annotations: Annotation[],
  opts: PaintOptions,
) => {
  const r = opts.rendition as unknown as { annotations: any } | null
  const store = r?.annotations
  if (!store) return

  const owned = ownedFor(store)
  // Wipe everything we previously injected. marks-pane stacks <g> elements
  // in DOM-insertion order, and its event proxy iterates them in reverse —
  // so "later add = drawn on top + first to receive clicks". To make sure
  // a short highlight ("Urantia") sits on top of an overlapping longer one
  // ("records of Urantia respecting"), we must re-add ALL annotations in
  // length-descending order on every paint. A diff-based update can't
  // enforce that ordering once items have been inserted, so we wipe.
  for (const cfi of owned) {
    try { store.remove(cfi, 'highlight') } catch (e) {
      console.warn('epub annotation: remove failed', cfi, e)
    }
  }
  owned.length = 0

  // Deduplicate by cfiRange. ePub.js's Annotations.add() keys its internal
  // store by encoded(cfiRange); a second add() at the same cfi overwrites the
  // map entry but marks-pane has already drawn a separate <g>, so the first
  // <g> becomes an unreachable orphan. The multi-user case (two people
  // annotating the same text) used to trigger this on every repaint. Sidebar
  // still lists every annotation by id; here we just paint one mark per
  // unique cfi. Representative preference: own first (so click opens the
  // user's editable annotation), then most recent.
  const byCfi = new Map<string, Annotation[]>()
  for (const ann of annotations) {
    const a = ann.anchor as EpubAnchor
    if (a?.type !== 'epub' || !a.cfiRange) continue
    const list = byCfi.get(a.cfiRange)
    if (list) list.push(ann)
    else byCfi.set(a.cfiRange, [ann])
  }

  const representatives: Annotation[] = []
  for (const list of byCfi.values()) {
    list.sort((a, b) => {
      if (a.is_own !== b.is_own) return a.is_own ? -1 : 1
      return a.created_at < b.created_at ? 1 : -1
    })
    representatives.push(list[0])
  }

  // Re-add longest first so a short highlight ("Urantia") sits on top of an
  // overlapping longer one ("records of Urantia respecting") — see the comment
  // at the top of paintAnnotations for the ordering rationale.
  representatives.sort((a, b) => b.selected_text.length - a.selected_text.length)

  for (const ann of representatives) {
    const a = ann.anchor as EpubAnchor
    // ePub.js → marks-pane → classList.add(this.className) only accepts a
    // single token (no spaces). Two classes like 'anno anno--mine' throw
    // InvalidCharacterError and the highlight is never added.
    const cls = ann.is_own ? 'anno-mine' : 'anno-other'
    const fill = ann.is_own ? FILL_MINE : FILL_OTHER
    try {
      store.add(
        'highlight',
        a.cfiRange,
        { __annoId: ann.id },
        opts.onClick ? () => opts.onClick!(ann.id) : undefined,
        cls,
        { fill, 'fill-opacity': FILL_OPACITY },
      )
      owned.push(a.cfiRange)
    } catch (e) {
      console.warn('epub annotation: add failed', a.cfiRange, e)
    }
  }
}

export const isEpubAnchor = (a: AnnotationAnchor): a is EpubAnchor => a.type === 'epub'
