<script setup lang="ts">
// Share a playlist. Private => "Make public & get link" CTA. Public => the
// shareable link + Copy, an amber clearance heads-up listing the gated book
// titles, and a Public->Private toggle. Performs share/unshare itself and emits
// the updated visibility/token so the parent can refresh.
import { ref, computed, inject, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  XMarkIcon, LinkIcon, GlobeAltIcon, LockClosedIcon,
  InformationCircleIcon, CheckCircleIcon, ClipboardDocumentIcon,
} from '@heroicons/vue/24/outline'
import { sharePlaylist, unsharePlaylist, notePlaylistLinkCopied, type PlaylistSummary, type PlaylistItem, type PlaylistVisibility } from '../api'

const props = defineProps<{ playlist: PlaylistSummary; items: PlaylistItem[] }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'updated', payload: { visibility: PlaylistVisibility; share_token: string | null }): void
}>()

const { t } = useI18n({ useScope: 'global' })

// The clearance heads-up names the clearance system, so it's admin-only
// (CLAUDE.md) — a non-admin owner must not learn clearance exists.
const currentUser = inject<Ref<{ is_admin?: boolean } | null>>('currentUser', ref(null))
const isAdmin = computed(() => !!currentUser.value?.is_admin)

const visibility = ref<PlaylistVisibility>(props.playlist.visibility)
const token = ref<string | null>(props.playlist.share_token)
const busy = ref(false)
const copied = ref(false)

// Hash-routing-safe public link: current path up to the hash + #/p/<token>.
const shareUrl = computed(() => {
  if (!token.value) return ''
  const base = window.location.origin + window.location.pathname
  return `${base}#/p/${token.value}`
})

// The heads-up reasons about BOOKS only: a book above the baseline clearance (0)
// is hidden from lower-clearance recipients. Directory visibility depends on
// each recipient's view of the contents (computed server-side), so we neither
// count directories as "everyone sees" nor claim a total over them — that's
// what previously overstated the figure.
const bookItems = computed(() => props.items.filter((it) => it.item_type === 'book'))
const gated = computed(() => bookItems.value.filter((it) => (it.clearance ?? 0) > 0))
const visibleToEveryone = computed(() => bookItems.value.length - gated.value.length)

const makePublic = async () => {
  if (busy.value) return
  busy.value = true
  try {
    const res = await sharePlaylist(props.playlist.id)
    visibility.value = res.data.visibility
    token.value = res.data.token
    emit('updated', { visibility: visibility.value, share_token: token.value })
  } finally {
    busy.value = false
  }
}

const makePrivate = async () => {
  if (busy.value) return
  busy.value = true
  try {
    const res = await unsharePlaylist(props.playlist.id)
    visibility.value = res.data.visibility
    emit('updated', { visibility: visibility.value, share_token: token.value })
  } finally {
    busy.value = false
  }
}

const copy = async () => {
  // Record the click intent (fire-and-forget), independent of the clipboard
  // write — which can fail on plain-HTTP LANs where navigator.clipboard is
  // unavailable, so gating telemetry on its success would undercount.
  notePlaylistLinkCopied(props.playlist.id).catch(() => { /* telemetry best-effort */ })
  try {
    await navigator.clipboard.writeText(shareUrl.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1800)
  } catch { /* ignore */ }
}
</script>

<template>
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
    role="dialog"
    aria-modal="true"
    @mousedown.self="emit('close')"
  >
    <div class="max-w-lg w-full bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-gray-200 dark:border-gray-700">
      <div class="flex items-center justify-between px-6 pt-5 pb-3">
        <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">{{ t('playlists.share_title') }}</h2>
        <button @click="emit('close')" class="p-1 rounded-md text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700">
          <XMarkIcon class="h-5 w-5" />
        </button>
      </div>

      <!-- Private -->
      <div v-if="visibility === 'private'" class="px-6 pb-6 space-y-4">
        <div class="rounded-lg bg-gray-100 dark:bg-gray-700/60 p-4">
          <div class="flex items-center gap-2 font-medium text-gray-800 dark:text-gray-200">
            <LockClosedIcon class="h-5 w-5" /> {{ t('playlists.make_public_title') }}
          </div>
          <p class="text-sm text-gray-600 dark:text-gray-400 mt-1">{{ t('playlists.make_public_body') }}</p>
        </div>
        <button
          @click="makePublic"
          :disabled="busy"
          class="w-full inline-flex items-center justify-center gap-2 px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50"
        >
          <GlobeAltIcon class="h-5 w-5" /> {{ t('playlists.make_public') }}
        </button>
      </div>

      <!-- Public -->
      <div v-else class="px-6 pb-6 space-y-4">
        <div>
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{ t('playlists.link_label') }}</label>
          <div class="flex gap-2">
            <div class="flex-1 flex items-center gap-2 px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-sm font-mono text-gray-700 dark:text-gray-300 truncate">
              <LinkIcon class="h-4 w-4 flex-shrink-0 text-gray-400" />
              <span class="truncate">{{ shareUrl }}</span>
            </div>
            <button
              @click="copy"
              class="inline-flex items-center gap-1 px-3 py-2 rounded-md text-sm font-medium text-white whitespace-nowrap"
              :class="copied ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700'"
            >
              <CheckCircleIcon v-if="copied" class="h-4 w-4" />
              <ClipboardDocumentIcon v-else class="h-4 w-4" />
              {{ copied ? t('playlists.copied') : t('playlists.copy') }}
            </button>
          </div>
        </div>

        <!-- Clearance heads-up (admin-only: names the clearance system) -->
        <div
          v-if="isAdmin && gated.length"
          class="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 p-4"
        >
          <div class="flex items-start gap-2">
            <InformationCircleIcon class="h-5 w-5 flex-shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
            <div class="text-sm text-amber-800 dark:text-amber-200">
              <p class="font-medium">{{ t('playlists.clearance_heads_up') }}</p>
              <p class="mt-0.5">{{ t('playlists.clearance_body', { n: gated.length }, gated.length) }}</p>
            </div>
          </div>
          <div class="mt-3 text-sm text-amber-800 dark:text-amber-200">
            <div class="flex items-center gap-1.5 font-medium">
              <CheckCircleIcon class="h-4 w-4" /> {{ t('playlists.everyone_sees', { n: visibleToEveryone }) }}
            </div>
            <ul class="mt-2 space-y-1">
              <li v-for="it in gated" :key="it.id" class="flex items-center justify-between gap-2">
                <span class="flex items-center gap-1.5 truncate">
                  <LockClosedIcon class="h-3.5 w-3.5 flex-shrink-0" />
                  <span class="truncate">{{ it.title || it.name }}</span>
                </span>
                <span class="px-1.5 py-0.5 rounded font-mono text-xs bg-amber-100 dark:bg-amber-900/60 border border-amber-300 dark:border-amber-700 whitespace-nowrap">🔒 {{ it.clearance ?? 0 }}</span>
              </li>
            </ul>
          </div>
        </div>

        <!-- Visibility toggle -->
        <div class="flex items-center justify-between pt-1">
          <span class="text-sm text-gray-500 dark:text-gray-400">{{ t('playlists.visibility_now') }}</span>
          <button
            @click="makePrivate"
            :disabled="busy"
            class="inline-flex items-center gap-1.5 text-sm font-medium text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white disabled:opacity-50"
          >
            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200">
              <GlobeAltIcon class="h-3.5 w-3.5" /> {{ t('playlists.public') }}
            </span>
            <span aria-hidden="true">→</span>
            <span class="inline-flex items-center gap-1">
              <LockClosedIcon class="h-3.5 w-3.5" /> {{ t('playlists.make_private') }}
            </span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
