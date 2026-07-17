import { ref, computed, watch, type Ref } from 'vue'
import {
  getAnnotations,
  createAnnotation,
  updateAnnotation,
  deleteAnnotation,
  type Annotation,
  type AnnotationAnchor,
  type AnnotationCreatePayload,
  type AnnotationUpdatePayload,
} from '../api'

export type AnnotationVisibility = 'all' | 'mine' | 'none'

const VISIBILITY_KEY = 'annotations-visibility'

const loadVisibility = (): AnnotationVisibility => {
  const v = localStorage.getItem(VISIBILITY_KEY)
  return v === 'mine' || v === 'none' ? v : 'all'
}

export function useAnnotations(hashId: Ref<string | null | undefined>) {
  const annotations = ref<Annotation[]>([])
  const loading = ref(false)
  const error = ref('')
  const visibility = ref<AnnotationVisibility>(loadVisibility())

  watch(visibility, (v) => {
    try { localStorage.setItem(VISIBILITY_KEY, v) } catch { /* quota */ }
  })

  const visible = computed<Annotation[]>(() => {
    if (visibility.value === 'none') return []
    if (visibility.value === 'mine') return annotations.value.filter(a => a.is_own)
    return annotations.value
  })

  // Annotations require a committed library book (a real hash). Foreign files
  // browsed off-disk and staging previews have no hash, so the whole annotation
  // UI (selection popover, creation) must stay dormant for them — text
  // selection and the native browser menu should work as on any plain page.
  const enabled = computed(() => !!hashId.value)

  // Monotonic token: the composable instance survives book switches inside a
  // viewer (destroy + re-init, not remount), so overlapping loads must not
  // resolve out of order and revive the previous book's list.
  let loadSeq = 0

  const load = async () => {
    const seq = ++loadSeq
    if (!hashId.value) {
      // Hashless source (staging preview / foreign file): the previous book's
      // annotations must not linger and repaint onto this document.
      annotations.value = []
      error.value = ''
      loading.value = false
      return
    }
    loading.value = true
    error.value = ''
    try {
      const res = await getAnnotations(hashId.value)
      if (seq !== loadSeq) return
      annotations.value = res.data.annotations
    } catch (e: any) {
      if (seq !== loadSeq) return
      if (e.response?.status === 403) {
        // Caller lacks clearance — silent, just empty list.
        annotations.value = []
      } else {
        error.value = e.response?.data?.detail || e.message || 'Failed to load annotations'
      }
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  const create = async (
    anchor: AnnotationAnchor,
    selectedText: string,
    opts: { body?: string | null; isPublic?: boolean; prefix?: string | null; suffix?: string | null } = {},
  ): Promise<Annotation | null> => {
    if (!hashId.value) return null
    const payload: AnnotationCreatePayload = {
      hash_id: hashId.value,
      anchor,
      selected_text: selectedText,
      text_prefix: opts.prefix ?? null,
      text_suffix: opts.suffix ?? null,
      body: opts.body ?? null,
      is_public: !!opts.isPublic,
    }
    const res = await createAnnotation(payload)
    annotations.value = [...annotations.value, res.data]
    return res.data
  }

  const update = async (id: number, payload: AnnotationUpdatePayload): Promise<Annotation | null> => {
    const res = await updateAnnotation(id, payload)
    annotations.value = annotations.value.map(a => a.id === id ? res.data : a)
    return res.data
  }

  const remove = async (id: number): Promise<void> => {
    await deleteAnnotation(id)
    annotations.value = annotations.value.filter(a => a.id !== id)
  }

  return {
    annotations,
    visible,
    loading,
    error,
    visibility,
    enabled,
    load,
    create,
    update,
    remove,
  }
}
