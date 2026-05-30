import { useI18n } from 'vue-i18n'
import api from '../api'

// Shared admin action behind the amber 🔒N clearance pill: prompt for a new
// clearance (0–100), PUT it, and reflect the new value on the passed item.
// Used by BrowseView, SearchView and PlaylistDetailView so the pill behaves
// identically everywhere. Callers handle their own click event (prevent/stop)
// before invoking editClearance. Must be called from a component setup()
// (it uses useI18n).
export interface ClearanceEditable {
  hash_id?: string | null
  clearance?: number | null
  title?: string | null
  name?: string | null
}

export function useEditClearance() {
  const { t } = useI18n({ useScope: 'global' })

  const editClearance = async (item: ClearanceEditable): Promise<void> => {
    if (!item.hash_id) return
    const current = item.clearance ?? 0
    const raw = window.prompt(
      t('admin.clearance_prompt', { title: item.title || item.name || '' }),
      String(current),
    )
    if (raw === null) return
    const next = Number(raw)
    if (!Number.isFinite(next) || !Number.isInteger(next) || next < 0 || next > 100) {
      alert(t('admin.integrity.clearance_invalid_range'))
      return
    }
    try {
      await api.put(`/admin/books/${encodeURIComponent(item.hash_id)}/clearance`, { clearance: next })
      item.clearance = next
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message)
    }
  }

  return { editClearance }
}
