// Anchor model for HTML/Markdown/FB2 viewers. The rendered content already
// peppers paragraph/heading elements with `data-anchor="N"` (used by the
// existing scroll-position progress tracking and TOC jump). We piggy-back on
// those as stable, edit-tolerant anchor points for annotations.

import type { Annotation, AnnotationAnchor } from '../../api'

const CONTEXT_LEN = 64

// Soft hyphens (U+00AD) are injected into the rendered text by lib/hyphenate.ts
// for cross-platform hyphenation. All offset bookkeeping below works in
// "logical" space that ignores them, so an offset means the same thing whether
// or not the DOM currently carries soft hyphens. This keeps annotations created
// before hyphenation valid and lets new ones store clean (shy-free) text.
const SHY = 0x00ad
const stripShy = (s: string): string => s.replace(/­/g, '')
// Count non-soft-hyphen characters in s.
const nonShyLen = (s: string): number => {
  let n = 0
  for (let i = 0; i < s.length; i++) if (s.charCodeAt(i) !== SHY) n++
  return n
}
// DOM offset within `data` after `logical` non-soft-hyphen characters.
const logicalToDomOffset = (data: string, logical: number): number => {
  let count = 0
  let i = 0
  while (i < data.length && count < logical) {
    if (data.charCodeAt(i) !== SHY) count++
    i++
  }
  return i
}

export interface HtmlAnchor {
  type: 'html'
  containerAnchor: number
  startOffset: number
  endContainerAnchor?: number
  endOffset: number
}

export interface AnchoredSelection {
  anchor: HtmlAnchor
  selectedText: string
  prefix: string
  suffix: string
}

const findEnclosingAnchor = (node: Node | null, container: HTMLElement): { el: HTMLElement; anchor: number } | null => {
  let n: Node | null = node
  while (n && n !== container) {
    if (n.nodeType === 1) {
      const el = n as HTMLElement
      const raw = el.dataset?.anchor
      if (raw != null && raw !== '') {
        const v = parseInt(raw)
        if (Number.isFinite(v)) return { el, anchor: v }
      }
    }
    n = n.parentNode
  }
  // Fallback: pin to container itself with synthetic anchor -1.
  return { el: container, anchor: -1 }
}

const findAnchorElement = (container: HTMLElement, anchor: number): HTMLElement => {
  if (anchor === -1) return container
  const el = container.querySelector<HTMLElement>(`[data-anchor="${anchor}"]`)
  return el || container
}

// Walk text nodes inside `root` and return character offset from root's text
// start to the (node, offset) point. Returns null when the point is outside
// root.
const pointToTextOffset = (root: HTMLElement, node: Node, nodeOffset: number): number | null => {
  if (!root.contains(node) && node !== root) return null
  // Handle element startContainer (selection starts before/after a child).
  let targetNode: Node = node
  let targetOffset: number = nodeOffset
  if (node.nodeType === 1) {
    const el = node as HTMLElement
    const child = el.childNodes[nodeOffset]
    if (child) {
      targetNode = child
      targetOffset = 0
    } else {
      // After the last child — total (logical) text length of the element.
      return nonShyLen(el.textContent ?? '')
    }
  }
  let total = 0
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let n = walker.nextNode() as Text | null
  while (n) {
    if (n === targetNode) return total + nonShyLen(n.data.slice(0, targetOffset))
    total += nonShyLen(n.data)
    n = walker.nextNode() as Text | null
  }
  return null
}

// Inverse of pointToTextOffset: locate (textNode, offsetInNode) at char `offset`
// from the start of `root`'s textContent.
const textOffsetToPoint = (root: HTMLElement, offset: number): { node: Text; offset: number } | null => {
  let remaining = offset
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let n = walker.nextNode() as Text | null
  let last: Text | null = null
  while (n) {
    const len = nonShyLen(n.data)
    if (remaining <= len) return { node: n, offset: logicalToDomOffset(n.data, remaining) }
    remaining -= len
    last = n
    n = walker.nextNode() as Text | null
  }
  if (last) return { node: last, offset: last.data.length }
  return null
}

export const anchorFromSelection = (container: HTMLElement, range: Range): AnchoredSelection | null => {
  const startInfo = findEnclosingAnchor(range.startContainer, container)
  const endInfo = findEnclosingAnchor(range.endContainer, container)
  if (!startInfo || !endInfo) return null

  const startOffset = pointToTextOffset(startInfo.el, range.startContainer, range.startOffset)
  const endOffset = pointToTextOffset(endInfo.el, range.endContainer, range.endOffset)
  if (startOffset == null || endOffset == null) return null

  // Store clean (soft-hyphen-free) text; offsets above are already logical.
  const selectedText = stripShy(range.toString())
  if (!selectedText) return null

  // Prefix/suffix from start/end element textContent for the text-quote
  // fallback — sliced in logical (shy-free) space to line up with the offsets.
  const startText = stripShy(startInfo.el.textContent ?? '')
  const endText = stripShy(endInfo.el.textContent ?? '')
  const prefix = startText.slice(Math.max(0, startOffset - CONTEXT_LEN), startOffset)
  const suffix = endText.slice(endOffset, endOffset + CONTEXT_LEN)

  const anchor: HtmlAnchor = {
    type: 'html',
    containerAnchor: startInfo.anchor,
    startOffset,
    endOffset,
  }
  if (endInfo.anchor !== startInfo.anchor) anchor.endContainerAnchor = endInfo.anchor

  return { anchor, selectedText, prefix, suffix }
}

// Remove every <mark.anno> created by a previous paintAnnotations call. Each
// mark is unwrapped (replaced by its children) and the parent is normalised
// so adjacent text nodes re-merge — restoring the DOM to its v-html shape.
export const unwrapMarks = (container: HTMLElement) => {
  const marks = container.querySelectorAll<HTMLElement>('mark.anno')
  marks.forEach((m) => {
    const parent = m.parentNode
    if (!parent) return
    while (m.firstChild) parent.insertBefore(m.firstChild, m)
    parent.removeChild(m)
  })
  container.normalize()
}

const buildRange = (container: HTMLElement, ann: Annotation): Range | null => {
  const anchor = ann.anchor as HtmlAnchor
  if (anchor.type !== 'html') return null
  const startEl = findAnchorElement(container, anchor.containerAnchor)
  const endEl = anchor.endContainerAnchor != null
    ? findAnchorElement(container, anchor.endContainerAnchor)
    : startEl

  let startPoint = textOffsetToPoint(startEl, anchor.startOffset)
  let endPoint = textOffsetToPoint(endEl, anchor.endOffset)
  let range: Range | null = null
  if (startPoint && endPoint) {
    range = document.createRange()
    try {
      range.setStart(startPoint.node, startPoint.offset)
      range.setEnd(endPoint.node, endPoint.offset)
      if (stripShy(range.toString()) === ann.selected_text) return range
    } catch { /* ranges that span disconnected nodes throw */ }
  }

  // Text-quote fallback: scan the container for prefix+selected+suffix. Both
  // the haystack and the stored needle are compared shy-free, and the resulting
  // index is a logical offset that textOffsetToPoint maps back to the DOM.
  const text = stripShy(container.textContent ?? '')
  const needle = (ann.text_prefix ?? '') + ann.selected_text + (ann.text_suffix ?? '')
  let idx = text.indexOf(needle)
  if (idx < 0) idx = text.indexOf(ann.selected_text)
  if (idx < 0) return null
  const startCharGlobal = idx + (ann.text_prefix?.length ?? 0)
  const endCharGlobal = startCharGlobal + ann.selected_text.length
  const sp = textOffsetToPoint(container, startCharGlobal)
  const ep = textOffsetToPoint(container, endCharGlobal)
  if (!sp || !ep) return null
  range = document.createRange()
  try {
    range.setStart(sp.node, sp.offset)
    range.setEnd(ep.node, ep.offset)
    return range
  } catch { return null }
}

// Wrap the text-node fragments inside `range` with <mark> elements bearing
// the given attributes. The range may cross element boundaries (paragraphs,
// headings); each text node intersected gets its own mark, all sharing the
// same data-annotation-id so click handling treats them as one annotation.
const wrapRangeWithMarks = (range: Range, attrs: { id: number; cls: string }) => {
  const texts: Text[] = []
  const walker = document.createTreeWalker(
    range.commonAncestorContainer,
    NodeFilter.SHOW_TEXT,
    {
      acceptNode(n) {
        return range.intersectsNode(n) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT
      },
    },
  )
  // Include start/end text nodes that intersectsNode may miss when commonAncestor
  // IS the text node itself.
  if (range.commonAncestorContainer.nodeType === 3) {
    texts.push(range.commonAncestorContainer as Text)
  } else {
    let n = walker.nextNode() as Text | null
    while (n) { texts.push(n); n = walker.nextNode() as Text | null }
  }

  for (const node of texts) {
    let from = 0
    let to = node.data.length
    if (node === range.startContainer) from = range.startOffset
    if (node === range.endContainer) to = range.endOffset
    if (from >= to) continue
    // Split off the leading/trailing portions we don't want to wrap.
    let target: Text = node
    if (from > 0) target = target.splitText(from)
    if (to - from < target.data.length) target.splitText(to - from)
    const mark = document.createElement('mark')
    mark.className = attrs.cls
    mark.dataset.annotationId = String(attrs.id)
    const parent = target.parentNode
    if (!parent) continue
    parent.insertBefore(mark, target)
    mark.appendChild(target)
  }
}

export interface PaintOptions {
  onClick?: (annotationId: number, event: MouseEvent) => void
}

export const paintAnnotations = (
  container: HTMLElement,
  annotations: Annotation[],
  opts: PaintOptions = {},
) => {
  unwrapMarks(container)

  // Longest-first so when annotations overlap (e.g. someone highlights a whole
  // phrase that contains a single word another user highlighted), the shorter,
  // more specific annotation ends up DOM-innermost. That makes it both visible
  // on top of the larger one and the one `closest('mark.anno')` returns from a
  // click, so clicking the inner word opens the inner author's note.
  const sorted = [...annotations].sort((a, b) => b.selected_text.length - a.selected_text.length)

  for (const ann of sorted) {
    const range = buildRange(container, ann)
    if (!range) continue
    const cls = ann.is_own ? 'anno anno--mine' : 'anno anno--other'
    try {
      wrapRangeWithMarks(range, { id: ann.id, cls })
    } catch { /* range crossed weird boundaries — skip */ }
  }

  if (opts.onClick && container.dataset.annoClickWired !== '1') {
    container.dataset.annoClickWired = '1'
    container.addEventListener('click', (e) => {
      const t = (e.target as HTMLElement | null)?.closest?.('mark.anno') as HTMLElement | null
      if (!t) return
      const id = parseInt(t.dataset.annotationId || '')
      if (Number.isFinite(id)) opts.onClick!(id, e)
    })
  }
}

// Paint a transient highlight for an in-progress (not-yet-saved) selection.
// Focusing the note textarea clears the native browser selection, so this keeps
// the user's selection visible while they type. Styled like the native
// selection (see `mark.anno--pending`) and non-interactive — the caller removes
// it with a plain repaint (paintAnnotations unwraps all marks).
export const paintPendingHighlight = (
  container: HTMLElement,
  anchor: HtmlAnchor,
  selectedText: string,
  prefix?: string | null,
  suffix?: string | null,
) => {
  if (anchor.type !== 'html') return
  const range = buildRange(container, {
    anchor,
    selected_text: selectedText,
    text_prefix: prefix ?? null,
    text_suffix: suffix ?? null,
  } as Annotation)
  if (!range) return
  try {
    wrapRangeWithMarks(range, { id: -1, cls: 'anno anno--pending' })
  } catch { /* skip on weird boundaries */ }
}

export const isHtmlAnchor = (a: AnnotationAnchor): a is HtmlAnchor => a.type === 'html'
