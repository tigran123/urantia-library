// Anchor model for the pdf.js text-layer viewer. Each rendered page has a
// `.textLayer` container whose direct child <span>s are the per-glyph-run
// text fragments in document order. We anchor by (page, spanIndex,
// offset-within-span).
//
// Caveats:
//  - Spans are re-built on every page render (zoom, fit toggle, page turn),
//    so paintAnnotations must be called each time renderPage finishes.
//  - Span indices are an artifact of pdf.js's chunking, not a stable property
//    of the document. They survive zoom changes (same chunking strategy) but
//    a major library upgrade could re-chunk the text. The selected_text +
//    prefix/suffix on the annotation row is the durable fallback used by the
//    common `findAnchor`-style text search below.

import type { Annotation, AnnotationAnchor } from '../../api'

const CONTEXT_LEN = 64

export interface PdfAnchor {
  type: 'pdf'
  page: number
  start: { spanIndex: number; offset: number }
  end: { spanIndex: number; offset: number }
}

export interface AnchoredSelection {
  anchor: PdfAnchor
  selectedText: string
  prefix: string
  suffix: string
}

const spanList = (textLayer: HTMLElement): HTMLElement[] =>
  Array.from(textLayer.querySelectorAll<HTMLElement>(':scope > span'))

const enclosingSpan = (node: Node | null, textLayer: HTMLElement): { el: HTMLElement; index: number } | null => {
  let n: Node | null = node
  while (n && n !== textLayer) {
    if (n.nodeType === 1 && (n as HTMLElement).parentElement === textLayer && n.nodeName === 'SPAN') {
      const el = n as HTMLElement
      const idx = spanList(textLayer).indexOf(el)
      if (idx >= 0) return { el, index: idx }
    }
    n = n.parentNode
  }
  return null
}

const pointToOffsetInSpan = (span: HTMLElement, node: Node, nodeOffset: number): number => {
  let total = 0
  const walker = document.createTreeWalker(span, NodeFilter.SHOW_TEXT)
  let n = walker.nextNode() as Text | null
  while (n) {
    if (n === node) return total + nodeOffset
    total += n.data.length
    n = walker.nextNode() as Text | null
  }
  // Selection point above element granularity: clamp to span text length.
  return span.textContent?.length ?? 0
}

const offsetInSpanToPoint = (span: HTMLElement, offset: number): { node: Text; offset: number } | null => {
  let remaining = offset
  const walker = document.createTreeWalker(span, NodeFilter.SHOW_TEXT)
  let n = walker.nextNode() as Text | null
  let last: Text | null = null
  while (n) {
    if (remaining <= n.data.length) return { node: n, offset: remaining }
    remaining -= n.data.length
    last = n
    n = walker.nextNode() as Text | null
  }
  if (last) return { node: last, offset: last.data.length }
  return null
}

export const anchorFromSelection = (
  textLayer: HTMLElement,
  page: number,
  range: Range,
): AnchoredSelection | null => {
  const startInfo = enclosingSpan(range.startContainer, textLayer)
  const endInfo = enclosingSpan(range.endContainer, textLayer)
  if (!startInfo || !endInfo) return null

  const startOffset = pointToOffsetInSpan(startInfo.el, range.startContainer, range.startOffset)
  const endOffset = pointToOffsetInSpan(endInfo.el, range.endContainer, range.endOffset)

  const selectedText = range.toString()
  if (!selectedText) return null

  const spans = spanList(textLayer)
  // Build a flat textContent and find the global offsets so we can extract
  // prefix/suffix for the text-quote fallback used at restore time.
  let pre = ''
  for (let i = 0; i < startInfo.index; i++) pre += spans[i].textContent ?? ''
  pre += (startInfo.el.textContent ?? '').slice(0, startOffset)
  let post = (endInfo.el.textContent ?? '').slice(endOffset)
  for (let i = endInfo.index + 1; i < spans.length; i++) post += spans[i].textContent ?? ''

  return {
    anchor: {
      type: 'pdf',
      page,
      start: { spanIndex: startInfo.index, offset: startOffset },
      end: { spanIndex: endInfo.index, offset: endOffset },
    },
    selectedText,
    prefix: pre.slice(-CONTEXT_LEN),
    suffix: post.slice(0, CONTEXT_LEN),
  }
}

export const unwrapMarks = (textLayer: HTMLElement) => {
  const marks = textLayer.querySelectorAll<HTMLElement>('mark.anno')
  marks.forEach((m) => {
    const parent = m.parentNode
    if (!parent) return
    while (m.firstChild) parent.insertBefore(m.firstChild, m)
    parent.removeChild(m)
  })
  // Don't normalize() the text layer — pdf.js relies on span internal structure.
}

const buildRange = (textLayer: HTMLElement, ann: Annotation): Range | null => {
  const a = ann.anchor as PdfAnchor
  if (a.type !== 'pdf') return null
  const spans = spanList(textLayer)
  const sSpan = spans[a.start.spanIndex]
  const eSpan = spans[a.end.spanIndex]
  if (sSpan && eSpan) {
    const sp = offsetInSpanToPoint(sSpan, a.start.offset)
    const ep = offsetInSpanToPoint(eSpan, a.end.offset)
    if (sp && ep) {
      const r = document.createRange()
      try {
        r.setStart(sp.node, sp.offset)
        r.setEnd(ep.node, ep.offset)
        if (r.toString() === ann.selected_text) return r
      } catch { /* fall through to text-quote */ }
    }
  }
  // Fallback: search the flattened text-layer text for prefix+selected+suffix.
  let flat = ''
  const spanStarts: number[] = []
  for (const s of spans) {
    spanStarts.push(flat.length)
    flat += s.textContent ?? ''
  }
  const needle = (ann.text_prefix ?? '') + ann.selected_text + (ann.text_suffix ?? '')
  let idx = flat.indexOf(needle)
  if (idx < 0) idx = flat.indexOf(ann.selected_text)
  if (idx < 0) return null
  const startGlobal = idx + (ann.text_prefix?.length ?? 0)
  const endGlobal = startGlobal + ann.selected_text.length

  const locate = (globalOffset: number): { node: Text; offset: number } | null => {
    let spanIdx = spanStarts.findIndex((start, i) => {
      const next = i + 1 < spanStarts.length ? spanStarts[i + 1] : flat.length
      return globalOffset >= start && globalOffset <= next
    })
    if (spanIdx < 0) spanIdx = spans.length - 1
    const within = globalOffset - spanStarts[spanIdx]
    return offsetInSpanToPoint(spans[spanIdx], within)
  }

  const sp = locate(startGlobal)
  const ep = locate(endGlobal)
  if (!sp || !ep) return null
  const r = document.createRange()
  try {
    r.setStart(sp.node, sp.offset)
    r.setEnd(ep.node, ep.offset)
    return r
  } catch { return null }
}

const wrapRangeWithMarks = (range: Range, attrs: { id: number; cls: string }) => {
  const texts: Text[] = []
  if (range.commonAncestorContainer.nodeType === 3) {
    texts.push(range.commonAncestorContainer as Text)
  } else {
    const walker = document.createTreeWalker(
      range.commonAncestorContainer,
      NodeFilter.SHOW_TEXT,
      { acceptNode(n) { return range.intersectsNode(n) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT } },
    )
    let n = walker.nextNode() as Text | null
    while (n) { texts.push(n); n = walker.nextNode() as Text | null }
  }
  for (const node of texts) {
    let from = 0
    let to = node.data.length
    if (node === range.startContainer) from = range.startOffset
    if (node === range.endContainer) to = range.endOffset
    if (from >= to) continue
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
  page: number
  onClick?: (annotationId: number, event: MouseEvent) => void
}

export const paintAnnotations = (
  textLayer: HTMLElement,
  annotations: Annotation[],
  opts: PaintOptions,
) => {
  unwrapMarks(textLayer)
  const onPage = annotations.filter((a) => {
    const anchor = a.anchor as PdfAnchor
    return anchor.type === 'pdf' && anchor.page === opts.page
  })
  // Longest-first: see html.ts paintAnnotations for rationale (shorter overlaps
  // must end up innermost so they're clickable and visually on top).
  onPage.sort((a, b) => b.selected_text.length - a.selected_text.length)

  for (const ann of onPage) {
    const range = buildRange(textLayer, ann)
    if (!range) continue
    try {
      wrapRangeWithMarks(range, {
        id: ann.id,
        cls: ann.is_own ? 'anno anno--mine' : 'anno anno--other',
      })
    } catch { /* skip on weird boundaries */ }
  }

  if (opts.onClick && textLayer.dataset.annoClickWired !== '1') {
    textLayer.dataset.annoClickWired = '1'
    textLayer.addEventListener('click', (e) => {
      const t = (e.target as HTMLElement | null)?.closest?.('mark.anno') as HTMLElement | null
      if (!t) return
      const id = parseInt(t.dataset.annotationId || '')
      if (Number.isFinite(id)) opts.onClick!(id, e)
    })
  }
}

export const isPdfAnchor = (a: AnnotationAnchor): a is PdfAnchor => a.type === 'pdf'
