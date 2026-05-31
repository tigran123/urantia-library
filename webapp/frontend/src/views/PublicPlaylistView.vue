<script setup lang="ts">
// /#/p/:token — public, read-only recipient view of a shared playlist. The
// server already clearance-filtered the items; gated ones are simply absent.
// "Save a copy" creates an owned copy (prompts sign-in for guests).
import { ref, computed, watch, onMounted, inject, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Squares2X2Icon, ListBulletIcon, BookmarkIcon, LinkSlashIcon, ArrowUturnLeftIcon } from '@heroicons/vue/24/outline'
import { BookmarkIcon as BookmarkIconSolid } from '@heroicons/vue/24/solid'
import CoverCollage from '../components/CoverCollage.vue'
import VisibilityBadge from '../components/VisibilityBadge.vue'
import PlaylistBookCard from '../components/PlaylistBookCard.vue'
import PlaylistBookRow from '../components/PlaylistBookRow.vue'
import AddToPlaylistPopover from '../components/AddToPlaylistPopover.vue'
import { gridItemSize, GRID_CLASSES } from '../composables/useGridItemSize'
import { formatShortDate } from '../lib/itemFormat'
import { playlistItemTarget } from '../composables/usePlaylistItem'
import { getSharedPlaylist, copySharedPlaylist, getContainedKeys, type SharedPlaylist, type PlaylistItem } from '../api'

const props = defineProps<{ token: string }>()
const router = useRouter()
const { t, locale } = useI18n({ useScope: 'global' })
const currentUser = inject<Ref<{ email?: string } | null>>('currentUser', ref(null))

const data = ref<SharedPlaylist | null>(null)
const loading = ref(true)
const notFound = ref(false)
const copying = ref(false)

const savedViewMode = localStorage.getItem('viewMode')
const viewMode = ref<'grid' | 'list'>(savedViewMode === 'list' ? 'list' : 'grid')
watch(viewMode, (v) => localStorage.setItem('viewMode', v))

const items = computed(() => data.value?.items ?? [])

const load = async () => {
  loading.value = true
  notFound.value = false
  try {
    const res = await getSharedPlaylist(props.token)
    data.value = res.data
  } catch {
    notFound.value = true
  } finally {
    loading.value = false
  }
}

// --- add-to-playlist (signed-in viewers add a shared item to THEIR own
// playlists, exactly like Browse). There's no "remove from this playlist" here
// — it isn't the viewer's playlist — so nothing ever disappears. The bookmark's
// blue state reflects the viewer's own membership (contained-keys).
const favoriteIds = ref<Set<string>>(new Set())
const dirFavorites = ref<Set<string>>(new Set())
const loadContainedKeys = async () => {
  if (!currentUser.value) { favoriteIds.value = new Set(); dirFavorites.value = new Set(); return }
  try {
    const res = await getContainedKeys()
    favoriteIds.value = new Set(res.data.book_hash_ids)
    dirFavorites.value = new Set(res.data.dir_paths)
  } catch (e) {
    console.error('Failed to load playlist membership', e)
  }
}
const isContained = (item: PlaylistItem) =>
  item.item_type === 'directory'
    ? !!(item.dir_path && dirFavorites.value.has(item.dir_path))
    : !!(item.hash_id && favoriteIds.value.has(item.hash_id))

type PopoverTarget = { book_hash_id?: string; dir_path?: string; title?: string }
const popoverTarget = ref<PopoverTarget | null>(null)
const popoverPos = ref<{ top: number; left: number }>({ top: 0, left: 0 })
const openPlaylistPopover = (item: PlaylistItem, event: Event) => {
  event.preventDefault()
  event.stopPropagation()
  if (!currentUser.value) return // bookmark is hidden for guests anyway
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  popoverPos.value = { top: rect.bottom + 4, left: rect.left }
  popoverTarget.value = playlistItemTarget(item)
}
const onMembershipChanged = () => { loadContainedKeys() }

onMounted(() => { load(); loadContainedKeys() })
watch(() => props.token, load)

const saveCopy = async () => {
  if (!currentUser.value) {
    router.push({ name: 'login', query: { next: router.currentRoute.value.fullPath } })
    return
  }
  if (copying.value) return
  copying.value = true
  try {
    const res = await copySharedPlaylist(props.token)
    router.push(`/playlists/${res.data.id}`)
  } catch (e) {
    console.error('save a copy failed', e)
    copying.value = false
  }
}
</script>

<template>
  <div class="max-w-7xl mx-auto space-y-6">
    <div v-if="loading" class="flex justify-center items-center py-20">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400"></div>
    </div>

    <div v-else-if="notFound || !data" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
      <LinkSlashIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
      <p class="text-lg">{{ t('playlists.link_unavailable') }}</p>
    </div>

    <template v-else>
      <!-- Owner banner: when you're previewing your own shared playlist, the
           shared-view chrome strips the app nav, so offer a one-click way back
           to the owner page (covers same-tab navigation too, not just new-tab). -->
      <router-link
        v-if="data.playlist.is_owner"
        :to="`/playlists/${data.playlist.id}`"
        class="inline-flex items-center gap-1.5 text-sm text-blue-600 dark:text-blue-400 hover:underline"
      >
        <ArrowUturnLeftIcon class="h-4 w-4" /> {{ t('playlists.back_to_owner_view') }}
      </router-link>

      <!-- Header card -->
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-100 dark:border-gray-700 p-6">
        <div class="flex gap-5">
          <div class="w-28 h-28 flex-shrink-0 rounded-lg overflow-hidden border border-gray-100 dark:border-gray-700">
            <CoverCollage :items="data.playlist.collage" />
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center justify-between gap-3 flex-wrap">
              <div class="flex items-center gap-2 flex-wrap">
                <h1 class="text-2xl font-bold text-gray-900 dark:text-white">{{ data.playlist.name }}</h1>
                <VisibilityBadge visibility="public" />
              </div>
              <button
                @click="saveCopy"
                :disabled="copying"
                class="inline-flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 whitespace-nowrap"
              >
                <BookmarkIconSolid class="h-4 w-4" />
                {{ currentUser ? t('playlists.save_copy') : t('playlists.sign_in_to_save') }}
              </button>
            </div>
            <p v-if="data.playlist.owner_name" class="text-sm text-gray-500 dark:text-gray-400 mt-1">
              {{ t('playlists.shared_by') }} <span class="font-semibold text-gray-700 dark:text-gray-200">{{ data.playlist.owner_name }}</span>
            </p>
            <p v-if="data.playlist.description" class="text-gray-600 dark:text-gray-300 mt-2">{{ data.playlist.description }}</p>
            <div class="mt-2 text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2 flex-wrap">
              <span class="font-semibold text-gray-700 dark:text-gray-200">{{ t('playlists.items_count', { n: data.playlist.item_count }, data.playlist.item_count) }}</span>
              <span>·</span>
              <span>{{ t('playlists.updated') }} {{ formatShortDate(locale, data.playlist.updated_at) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- View toggle -->
      <div v-if="items.length" class="flex justify-end">
        <div class="flex bg-gray-100 dark:bg-gray-900 rounded-lg p-1 border border-transparent dark:border-gray-700">
          <button @click="viewMode = 'grid'" :class="['p-1.5 rounded-md transition-colors', viewMode === 'grid' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']" :title="t('app.grid_view')">
            <Squares2X2Icon class="h-5 w-5" />
          </button>
          <button @click="viewMode = 'list'" :class="['p-1.5 rounded-md transition-colors', viewMode === 'list' ? 'bg-white dark:bg-gray-700 shadow-sm text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200']" :title="t('app.list_view')">
            <ListBulletIcon class="h-5 w-5" />
          </button>
        </div>
      </div>

      <!-- Empty: the playlist has no books to show this viewer. Deliberately
           neutral — never hint that a clearance system filtered anything. -->
      <div v-if="items.length === 0" class="text-center py-20 text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 rounded-xl border border-gray-100 dark:border-gray-700 shadow-sm">
        <BookmarkIcon class="mx-auto h-12 w-12 text-gray-300 dark:text-gray-600 mb-3" />
        <p class="text-lg">{{ t('playlists.nothing_visible') }}</p>
      </div>

      <!-- Grid -->
      <div v-else-if="viewMode === 'grid'" :class="['grid', GRID_CLASSES[gridItemSize]]">
        <PlaylistBookCard v-for="item in items" :key="item.id" :item="item" mode="public" :contained="isContained(item)" @open-menu="(ev) => openPlaylistPopover(item, ev)" />
      </div>

      <!-- List -->
      <div v-else class="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700 overflow-hidden">
        <ul class="divide-y divide-gray-100 dark:divide-gray-700">
          <li v-for="item in items" :key="item.id" class="p-4 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
            <PlaylistBookRow :item="item" mode="public" :contained="isContained(item)" @open-menu="(ev) => openPlaylistPopover(item, ev)" />
          </li>
        </ul>
      </div>
    </template>

    <AddToPlaylistPopover v-if="popoverTarget" :position="popoverPos" :target="popoverTarget" @close="popoverTarget = null" @changed="onMembershipChanged" />
  </div>
</template>
