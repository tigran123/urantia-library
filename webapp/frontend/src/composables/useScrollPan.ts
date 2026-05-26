import { ref, watch, onBeforeUnmount, type Ref } from 'vue'

// Drag-to-pan for any scrollable container: mousedown begins a drag, subsequent
// mousemove drives the container's scrollLeft/scrollTop. Mirrors what ImageViewer
// does via CSS transform, but for viewers (PDF, DjVu) whose page content lives
// inside a native overflow:auto scroll box.
//
// `shouldPan` lets the caller veto a mousedown — used by PdfViewer to keep text
// selection working when the user has clicked inside the .textLayer overlay.
export function useScrollPan(
  target: Ref<HTMLElement | null>,
  shouldPan?: (e: MouseEvent) => boolean,
) {
  const isDragging = ref(false)
  const canPan = ref(false)

  let startX = 0
  let startY = 0
  let startScrollX = 0
  let startScrollY = 0

  const updateCanPan = () => {
    const el = target.value
    canPan.value = !!el && (el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight)
  }

  const onWindowMouseMove = (e: MouseEvent) => {
    if (!isDragging.value) return
    const el = target.value
    if (!el) return
    el.scrollLeft = startScrollX - (e.clientX - startX)
    el.scrollTop = startScrollY - (e.clientY - startY)
  }

  const onWindowMouseUp = () => {
    if (!isDragging.value) return
    isDragging.value = false
    window.removeEventListener('mousemove', onWindowMouseMove)
    window.removeEventListener('mouseup', onWindowMouseUp)
  }

  const onMouseDown = (e: MouseEvent) => {
    if (e.button !== 0) return
    const el = target.value
    if (!el) return
    updateCanPan()
    if (!canPan.value) return
    if (shouldPan && !shouldPan(e)) return
    isDragging.value = true
    startX = e.clientX
    startY = e.clientY
    startScrollX = el.scrollLeft
    startScrollY = el.scrollTop
    // Suppress browser-native drags (image ghost, text selection) so the pan
    // takes over cleanly.
    e.preventDefault()
    window.addEventListener('mousemove', onWindowMouseMove)
    window.addEventListener('mouseup', onWindowMouseUp)
  }

  let ro: ResizeObserver | null = null
  watch(target, (el) => {
    ro?.disconnect()
    ro = null
    if (!el) return
    ro = new ResizeObserver(() => updateCanPan())
    ro.observe(el)
    updateCanPan()
  }, { immediate: true })

  onBeforeUnmount(() => {
    ro?.disconnect()
    ro = null
    window.removeEventListener('mousemove', onWindowMouseMove)
    window.removeEventListener('mouseup', onWindowMouseUp)
  })

  return { isDragging, canPan, onMouseDown, updateCanPan }
}
