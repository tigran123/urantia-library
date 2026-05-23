// Shared helper: given the bounding rect of a selection (or an existing
// highlight mark) and the scroll/positioning container the popover lives in,
// place the popover directly below the selection, horizontally centered,
// with explicit width and clamping so it never overflows the viewport — on a
// narrow mobile viewport a left/right placement falls off-screen, but a
// below-the-selection placement with a width capped at `container.clientWidth
// - 16px` stays reachable.

export interface PopoverPosition {
  left: number       // in container-local coords
  top: number        // in container-local coords (popover top edge)
  width: number      // explicit width applied to the popover
}

const POPOVER_MAX_W = 360
const POPOVER_MIN_W = 200
const SIDE_MARGIN = 8
const GAP_BELOW = 6

function placeBelow(
  rect: { left: number; right: number; bottom: number; width: number },
  cr: DOMRect,
  scrollLeft: number,
  scrollTop: number,
  containerW: number,
): PopoverPosition {
  const width = Math.max(POPOVER_MIN_W, Math.min(POPOVER_MAX_W, containerW - 2 * SIDE_MARGIN))
  const xCenterLocal = rect.left + rect.width / 2 - cr.left
  const minLeft = SIDE_MARGIN
  const maxLeft = containerW - width - SIDE_MARGIN
  let left = xCenterLocal - width / 2
  if (left < minLeft) left = minLeft
  if (left > maxLeft) left = maxLeft
  const top = rect.bottom - cr.top + GAP_BELOW
  // Translate from container-viewport-local to container-content (account for scroll).
  return { left: left + scrollLeft, top: top + scrollTop, width }
}

export function popoverPosition(
  rect: DOMRect | { left: number; right: number; top: number; bottom: number; width: number; height: number },
  container: HTMLElement,
): PopoverPosition {
  return placeBelow(
    rect,
    container.getBoundingClientRect(),
    container.scrollLeft,
    container.scrollTop,
    container.clientWidth,
  )
}

// Variant for EPUB: the selection rect lives in the parent-document viewport
// (we already translate iframe-local coords up by adding the iframe's offset),
// and the popover host doesn't scroll, so scrollLeft/scrollTop are 0.
export function popoverPositionFromViewport(
  rect: { left: number; right: number; top: number; bottom: number; width: number; height: number },
  container: HTMLElement,
): PopoverPosition {
  return placeBelow(rect, container.getBoundingClientRect(), 0, 0, container.clientWidth)
}
