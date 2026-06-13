import { ref } from 'vue'
import api, { type CurrentUser } from './api'

// Shared signed-in-user state. This used to live as a ref inside App.vue's setup
// and was handed to the tree via provide('currentUser', …). It was lifted here so
// the router's beforeEach guard can *await* auth resolution before deciding an
// /admin/* navigation (a guard runs before any component mounts, so it can't rely
// on App.vue having fetched /me yet). App.vue still imports this ref and provides
// it under the same key, so every inject('currentUser') consumer is unaffected.
export const currentUser = ref<CurrentUser | null>(null)

// Dedup concurrent /me fetches (e.g. the guard and App.vue's onMounted both wanting
// it on a cold load) onto one in-flight request.
let inflight: Promise<CurrentUser | null> | null = null
function loadMe(): Promise<CurrentUser | null> {
  if (inflight) return inflight
  inflight = api.get<CurrentUser>('/me')
    .then((r) => { currentUser.value = r.data; return currentUser.value })
    .catch(() => { currentUser.value = null; return null })
    .finally(() => { inflight = null })
  return inflight
}

// Force a fresh /me. App.vue's fetchCurrentUser + heartbeat use this to keep their
// existing "always refetch" semantics.
export const refreshCurrentUser = () => loadMe()

// Used by the router's admin guard. Short-circuits when a user is already loaded so
// in-app admin navigations don't re-hit /me on every tab switch. Staleness (e.g. a
// just-demoted admin) is bounded by App.vue's heartbeat, and every /api/admin/* call
// is 403-gated server-side, so the guard is UX only.
export const ensureAuthLoaded = (): Promise<CurrentUser | null> =>
  currentUser.value ? Promise.resolve(currentUser.value) : loadMe()
