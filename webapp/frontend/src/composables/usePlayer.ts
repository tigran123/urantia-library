// App-level audio player — a module-scope reactive singleton (the project's
// convention for shared state; see useGridItemSize.ts / lib/pendingImports.ts).
// One shared HTMLAudioElement backs continuous playback so the now-playing bar
// keeps playing while the user navigates between routes. The bar and the Album
// view both read/drive this store; nothing here is per-view.
import { ref, reactive, watch } from 'vue'
import { fileUrl } from '../lib/assets'

export interface AlbumTrack {
  id: string                 // stable per row — the file path
  name: string
  path: string               // relative path, for /api/files/<path>
  title: string
  artist: string             // composer (item.author)
  cover_url: string | null
  size: number | null
  hash_id?: string
  rating: number | null      // avg_rating
  ratingCount: number
  fmt: string                // fileTypeLabel(name)
  duration?: number | null   // seconds, from the backend (ffprobe at import)
  bitrate?: number | null    // bits/sec, from the backend (ffprobe at import)
  groupPath?: string         // recursive grouping: owning subdirectory path
  groupName?: string         // recursive grouping: subdirectory display name
}

export type RepeatMode = 'off' | 'all' | 'one'

// A navigable Album-view location: the rooted directory, its display name, and
// whether "Include subdirectories" was on. Used for the now-playing bar's
// "back to where I started" link.
export interface AlbumOrigin {
  path: string
  name: string
  recursive: boolean
}

// ---- singleton state ----
const currentTrack = ref<AlbumTrack | null>(null)
const isPlaying = ref(false)
const elapsed = ref(0)
const duration = ref(0)
const shuffle = ref(false)
const repeat = ref<RepeatMode>('off')
const queue = ref<AlbumTrack[]>([])

// Where playback can be navigated back to. `viewContext` is the Album view
// currently publishing the queue (updated on every navigation / recursive
// toggle); `playbackOrigin` is a snapshot taken when a user-initiated play
// starts, so it stays pinned to where playback actually began even as the user
// browses other albums or tracks auto-advance.
const viewContext = ref<AlbumOrigin | null>(null)
const playbackOrigin = ref<AlbumOrigin | null>(null)

// Tracks the user has excluded from sequential/random playback, keyed by path.
// A direct click still plays an excluded track — exclusion only affects
// next/prev/auto-advance/shuffle. Session-only (in memory).
const excluded = reactive(new Set<string>())
const isExcluded = (path: string): boolean => excluded.has(path)

const VOL_KEY = 'playerVolume'
// `getItem` returns null when unset, and `Number(null)` is 0 — an in-range value
// that would pass the check below and mute first-time users. Treat null/empty as
// "unset" so the 72 default actually applies.
const storedVolRaw = localStorage.getItem(VOL_KEY)
const storedVol = storedVolRaw == null || storedVolRaw === '' ? NaN : Number(storedVolRaw)
const volume = ref<number>(Number.isFinite(storedVol) && storedVol >= 0 && storedVol <= 100 ? storedVol : 72)

// Durations come from the backend (ffprobe at import, in track.duration). This
// cache only holds the *live* value read from `loadedmetadata` for tracks that are
// actually played — a refinement (and a fallback when a row's server duration is
// NULL because ffprobe couldn't read it). No client-side probing of unplayed tracks.
const durationCache = reactive<Record<string, number>>({})
const trackDur = (track: AlbumTrack): number | undefined =>
  durationCache[track.path] ?? track.duration ?? undefined

// ---- shared audio element (lazily created on first interaction) ----
let audio: HTMLAudioElement | null = null
function getAudio(): HTMLAudioElement {
  if (audio) return audio
  const a = new Audio()
  a.volume = volume.value / 100
  a.addEventListener('timeupdate', () => { elapsed.value = a.currentTime })
  a.addEventListener('loadedmetadata', () => {
    duration.value = a.duration || 0
    if (currentTrack.value && a.duration && isFinite(a.duration)) {
      durationCache[currentTrack.value.path] = a.duration
    }
  })
  a.addEventListener('play', () => { isPlaying.value = true })
  a.addEventListener('pause', () => { isPlaying.value = false })
  a.addEventListener('ended', onEnded)
  audio = a
  return a
}

watch(volume, (v) => {
  if (audio) audio.volume = v / 100
  localStorage.setItem(VOL_KEY, String(Math.round(v)))
})

// ---- transport ----
// User-initiated start from the active Album view — pins the back-link origin
// to the view as it is right now. Auto-advance (next/prev/onEnded) calls the
// low-level `play` instead, leaving the origin untouched.
function playFromView(track: AlbumTrack) {
  playbackOrigin.value = viewContext.value
  play(track)
}

function play(track: AlbumTrack) {
  const a = getAudio()
  if (!currentTrack.value || currentTrack.value.path !== track.path) {
    currentTrack.value = track
    elapsed.value = 0
    duration.value = durationCache[track.path] ?? track.duration ?? 0
    a.src = fileUrl(track.path)
  }
  a.play().catch(() => { /* autoplay/gesture rejection — ignore */ })
}

function togglePlay() {
  if (!currentTrack.value) return
  const a = getAudio()
  if (a.paused) a.play().catch(() => {})
  else a.pause()
}

const indexInQueue = (t: AlbumTrack | null): number =>
  t ? queue.value.findIndex((x) => x.id === t.id) : -1

function next() {
  const q = queue.value
  if (!currentTrack.value || !q.length) return
  if (shuffle.value) {
    const pool = q.filter((t) => !excluded.has(t.path) && t.id !== currentTrack.value!.id)
    if (pool.length) { play(pool[Math.floor(Math.random() * pool.length)]); return }
    if (repeat.value === 'all') {
      const only = q.find((t) => !excluded.has(t.path))
      if (only) { play(only); return }
    }
    if (audio) audio.pause()
    return
  }
  const i = indexInQueue(currentTrack.value)
  for (let k = i + 1; k < q.length; k++) {
    if (!excluded.has(q[k].path)) { play(q[k]); return }
  }
  // No playable track after the current one.
  if (repeat.value === 'all' || i < 0) {
    const first = q.find((t) => !excluded.has(t.path))
    if (first) { play(first); return }
  }
  if (audio) audio.pause() // reached the end
}

function prev() {
  if (!currentTrack.value) return
  const a = getAudio()
  if (elapsed.value > 3) { a.currentTime = 0; elapsed.value = 0; return }
  const q = queue.value
  const i = indexInQueue(currentTrack.value)
  for (let k = i - 1; k >= 0; k--) {
    if (!excluded.has(q[k].path)) { play(q[k]); return }
  }
  // No earlier playable track — restart the current one.
  a.currentTime = 0
  elapsed.value = 0
}

function seek(sec: number) {
  const a = getAudio()
  a.currentTime = sec
  elapsed.value = sec
}

function setVolume(v: number) { volume.value = Math.max(0, Math.min(100, v)) }
function cycleRepeat() { repeat.value = repeat.value === 'off' ? 'all' : repeat.value === 'all' ? 'one' : 'off' }
function toggleShuffle() { shuffle.value = !shuffle.value }
// "Play all" = sequential playback from the top, so it also turns shuffle off.
// Play-all and Shuffle thus act as a mutually-exclusive playback-mode pair.
function playAll() {
  shuffle.value = false
  const first = queue.value.find((t) => !excluded.has(t.path))
  if (first) { playbackOrigin.value = viewContext.value; play(first) }
}
function shuffleAll() {
  const playable = queue.value.filter((t) => !excluded.has(t.path))
  if (!playable.length) return
  shuffle.value = true
  playbackOrigin.value = viewContext.value
  play(playable[Math.floor(Math.random() * playable.length)])
}

function onEnded() {
  if (repeat.value === 'one' && currentTrack.value) {
    const a = getAudio()
    a.currentTime = 0
    a.play().catch(() => {})
    return
  }
  next()
}

// The active Album view publishes its ordered audio items as the queue, plus
// its navigable context. This never interrupts the currently-playing track —
// only next/prev follow the queue, and the back-link origin is only captured
// on a user-initiated play.
function setQueue(tracks: AlbumTrack[], viewCtx: AlbumOrigin) {
  queue.value = tracks
  viewContext.value = viewCtx
}

// Stop playback and dismiss the now-playing bar (it renders nothing once
// currentTrack is null). Driven by the bar's close button.
function stop() {
  if (audio) {
    audio.pause()
    audio.removeAttribute('src')
    audio.load()
  }
  currentTrack.value = null
  isPlaying.value = false
  elapsed.value = 0
  duration.value = 0
}

// Full teardown for an identity change (logout / account switch). stop() alone
// leaves the queue and navigable context behind — and those carry the prior
// user's track titles, covers and *directory names* — so the next session in
// this browser could still see (or deep-link into) a higher-clearance album.
// reset() wipes everything the singleton holds so nothing survives the handover.
function reset() {
  stop()
  queue.value = []
  playbackOrigin.value = null
  viewContext.value = null
  excluded.clear()
  for (const k of Object.keys(durationCache)) delete durationCache[k]
}

// Toggle a track's exclusion from sequential/random playback. Excluding the
// track that's currently playing stops it (per spec) — we pause rather than
// advance, so it stays current and the user can still resume it manually.
function toggleExcluded(track: AlbumTrack) {
  if (excluded.has(track.path)) {
    excluded.delete(track.path)
  } else {
    excluded.add(track.path)
    if (currentTrack.value?.path === track.path && audio && !audio.paused) audio.pause()
  }
}

function downloadTrack(track: AlbumTrack) {
  const a = document.createElement('a')
  a.href = fileUrl(track.path)
  a.download = track.name
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

export function usePlayer() {
  return {
    // state
    currentTrack, isPlaying, elapsed, duration, shuffle, repeat, volume, queue, playbackOrigin,
    trackDur, isExcluded,
    // actions
    play, playFromView, togglePlay, next, prev, seek, setVolume, cycleRepeat, toggleShuffle,
    playAll, shuffleAll, setQueue, stop, reset, toggleExcluded, downloadTrack,
  }
}
