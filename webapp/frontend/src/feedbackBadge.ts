import { ref } from 'vue'
import { adminListFeedback } from './api'

// Shared reactive count for the AdminNav "Feedback" badge. "Active" = any
// thread not yet dispatched, i.e. status NOT in resolved/closed/archived.
// Updated on AdminNav mount + on each route change, and after any mutating
// action inside AdminFeedbackView so the badge stays current without polling.
export const feedbackActiveCount = ref(0)

const NON_TERMINAL = ['new', 'open', 'triage', 'progress', 'waiting'] as const

export async function refreshFeedbackActiveCount(): Promise<void> {
  try {
    // `counts` is visibility-scoped server-side (the same broadcast-or-mine
    // filter the list query uses), so directed-away threads don't bleed in.
    const r = await adminListFeedback('all', 1, 1)
    const c = r.data.counts || {}
    feedbackActiveCount.value = NON_TERMINAL.reduce(
      (acc, k) => acc + (Number(c[k]) || 0),
      0,
    )
  } catch {
    feedbackActiveCount.value = 0
  }
}
