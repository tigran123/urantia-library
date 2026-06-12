import { computed, watch, onScopeDispose } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// Immersive (fullscreen reading) mode is persisted in the route query as
// `immersive=1` so it survives a browser refresh — the viewer remounts, but the
// flag is read back off the URL instead of resetting to false. router.replace
// (not push) avoids piling up history entries; the query spread preserves sibling
// params (e.g. ?from=). vue-router omits query keys whose value is undefined,
// which is how we drop the param on exit.
//
// This composable also owns the universal body/document scroll lock (immediate
// so a refresh-into-immersive re-locks at mount) and releases it on scope dispose.
// Viewer-specific reactions (closing the TOC, stashing inline resize sizes) stay
// in each viewer.
export function useImmersive() {
  const route = useRoute()
  const router = useRouter()

  const immersive = computed<boolean>({
    get: () => route.query.immersive === '1',
    set: (v) => {
      if (v === (route.query.immersive === '1')) return
      router.replace({ query: { ...route.query, immersive: v ? '1' : undefined } })
    },
  })

  const toggleImmersive = () => { immersive.value = !immersive.value }

  watch(immersive, (v) => {
    document.body.style.overflow = v ? 'hidden' : ''
    document.documentElement.style.overflow = v ? 'hidden' : ''
  }, { immediate: true })

  onScopeDispose(() => {
    document.body.style.overflow = ''
    document.documentElement.style.overflow = ''
  })

  return { immersive, toggleImmersive }
}
