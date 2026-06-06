import { computed, inject, ref, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { fileTypeLabel } from '../lib/itemFormat'
import { recommendedTooltip } from '../lib/recommended'
import { fileUrl } from '../lib/assets'
import type { PlaylistItem } from '../api'

// Build the AddToPlaylistPopover `target` from a playlist item. Books carry
// their hash in `item.hash_id` but the popover wants the key `book_hash_id`;
// directories use `dir_path`. Shared by both playlist views so the mapping
// lives in one place.
export function playlistItemTarget(item: PlaylistItem): { book_hash_id?: string; dir_path?: string; title?: string } {
  const title = item.title || item.name || item.dir_path || ''
  return item.item_type === 'directory'
    ? { dir_path: item.dir_path, title }
    : { book_hash_id: item.hash_id, title }
}

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
  // The bookmark / add-to-playlist control needs a signed-in user (owners
  // always are; anonymous viewers of a shared link don't get it).
  const canBookmark = computed(() => !!currentUser.value)

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
    const url = fileUrl(props.item.path)
    const a = document.createElement('a')
    a.href = url
    a.download = props.item.name || ''
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  return { t, locale, isAdmin, isDir, isOwner, canBookmark, to, typeLabel, displayTitle, recTip, download }
}
