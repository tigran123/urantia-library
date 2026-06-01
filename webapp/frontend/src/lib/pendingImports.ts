import { ref } from 'vue'

// One-shot handoff of server file paths from the Browse "Import to library"
// action to the Admin Upload view. Browse sets this then navigates to
// /admin/upload; AdminUploadView consumes and clears it on mount. A module-level
// ref (not a route query) avoids stuffing many long paths into the hash-route URL.
export const pendingServerImports = ref<string[]>([])
