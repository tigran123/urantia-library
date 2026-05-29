// Tooltip text for the "recommended" (знак качества) badge.
//
// When we know who recommended the book and when, we render the rich form
// "Recommended by {name} on {date} at {time}" (localised); otherwise we fall
// back to the plain "Recommended" label. The date is formatted as
// "D MMM YYYY" + 24h time in the active locale, with the Russian " г." year
// suffix stripped so it reads "28 мая 2026 в 23:57" rather than
// "28 мая 2026 г. в 23:57".

type TFn = (key: string, params?: Record<string, unknown>) => string

// Format the stored ISO timestamp into localised { date, time } parts:
// date = "D MMMM YYYY" (Russian " г." suffix stripped), time = 24h "HH:mm".
// Returns null when the input is missing/unparseable.
function dateTimeParts(locale: string, at?: string | null): { date: string, time: string } | null {
  if (!at) return null
  const d = new Date(at)
  if (isNaN(d.getTime())) return null
  const intlLocale = locale === 'ru' ? 'ru-RU' : 'en-GB'
  const date = new Intl.DateTimeFormat(intlLocale, {
    day: 'numeric', month: 'long', year: 'numeric',
  }).format(d).replace(/\s*г\.?$/u, '')
  const time = new Intl.DateTimeFormat(intlLocale, {
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(d)
  return { date, time }
}

// Hover tooltip on the знак качества badge.
export function recommendedTooltip(
  t: TFn,
  locale: string,
  name?: string | null,
  at?: string | null,
): string {
  const parts = name ? dateTimeParts(locale, at) : null
  if (!name || !parts) return t('app.recommended_badge_tooltip')
  return t('app.recommended_by_tooltip', { name, date: parts.date, time: parts.time })
}

// Value for the ItemView "Recommended by" Details row (no verb prefix — the
// row label supplies it). Empty string when who/when is unknown.
export function recommendedByValue(
  t: TFn,
  locale: string,
  name?: string | null,
  at?: string | null,
): string {
  const parts = name ? dateTimeParts(locale, at) : null
  if (!name || !parts) return ''
  return t('app.recommended_by_value', { name, date: parts.date, time: parts.time })
}

// Shape returned by both /api/admin/books/recommend/bulk and
// /api/admin/books/unrecommend/bulk — `done` is the action's count
// (`recommended` or `unrecommended`); `skipped` is `unchanged`.
type BulkResult = {
  recommended?: number
  unrecommended?: number
  unchanged: number
  errors: { hash_id: string; reason: string }[]
}

// Show the per-error breakdown after a bulk recommend/unrecommend, but only
// when there were errors — a clean run gets a silent success (the UI patches
// the affected items in-place). Mirrors the BrowseView and SearchView usage
// so the two stay in sync; per feedback_bulk_select_bar_duplicated_browse_search.md
// the alert formatting must not drift between the views.
export function bulkResultAlert(
  t: TFn,
  verb: 'recommend' | 'unrecommend',
  res: BulkResult,
): void {
  if (!res.errors || !res.errors.length) return
  const done = verb === 'recommend' ? (res.recommended ?? 0) : (res.unrecommended ?? 0)
  const summary = t(`app.${verb}_bulk_summary`, {
    done, skipped: res.unchanged, failed: res.errors.length,
  })
  const lines = res.errors.map(e => `${e.hash_id.slice(0, 12)}…: ${e.reason}`).join('\n')
  alert(`${summary}\n${lines}`)
}
