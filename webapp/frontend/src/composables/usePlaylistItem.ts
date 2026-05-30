import { computed, inject, ref, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { fileTypeLabel } from '../lib/itemFormat'
import { recommendedTooltip } from '../lib/recommended'
import { getFullUrl } from '../lib/assets'
import type { PlaylistItem } from '../api'

// Shared logic for the two playlist item renderers (PlaylistBookCard grid +
// PlaylistBookRow list): both derive the same nav target, type badge, title,
// recommendation tooltip and blob-download from the item, and both gate the
// clearance pill on admin. Kept here so a change to e.g. the ?from= link
// propagation or download handling can't drift between the two cards.
export function usePlaylistItem(props: {
  item: PlaylistItem
  mode: 'owner' | 'public'
  from?: string | null
}) {
  const { t, locale } = useI18n({ useScope: 'global' })

  // Clearance must never be disclosed to non-admins (CLAUDE.md): callers gate
  // the 🔒N pill on isAdmin, matching Browse/Search.
  const currentUser = inject<Ref<{ is_admin?: boolean } | null>>('currentUser', ref(null))
  const isAdmin = computed(() => !!currentUser.value?.is_admin)

  const isDir = computed(() => props.item.item_type === 'directory')
  const isOwner = computed(() => props.mode === 'owner')
  const to = computed(() => {
    if (props.item.path == null) return null
    const path = isDir.value ? `/browse/${props.item.path}` : `/item/${props.item.path}`
    return props.from ? { path, query: { from: props.from } } : path
  })
  const typeLabel = computed(() => (isDir.value ? null : fileTypeLabel(props.item.name || '')))
  const displayTitle = computed(() => props.item.title || props.item.name || props.item.dir_path || '')
  const recTip = () => recommendedTooltip(t, locale.value, props.item.recommended_by_name, props.item.recommended_at)

  const download = (event: Event) => {
    event.preventDefault()
    event.stopPropagation()
    if (!props.item.path || isDir.value) return
    const url = getFullUrl(`/api/files/${props.item.path.split('/').map(encodeURIComponent).join('/')}`)
    const a = document.createElement('a')
    a.href = url
    a.download = props.item.name || ''
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  return { t, locale, isAdmin, isDir, isOwner, to, typeLabel, displayTitle, recTip, download }
}
