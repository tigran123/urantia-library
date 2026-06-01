// Shared types for the (multi-)book upload flow. Used by AdminUploadView (the
// orchestrator) and UploadItemEditor (the per-item review form).
import { basename } from '../../lib/volume'

export type Metadata = {
  title: string | null
  author: string | null
  publisher: string | null
  published: string | null
  description: string | null
  tags: string | null
  series: string | null
  languages: string | null
  identifiers: string | null
}

export const DEFAULT_META: Metadata = {
  title: '', author: '', publisher: '', published: '',
  description: '', tags: '', series: '', languages: '', identifiers: '',
}

// Metadata fields copied from the first item to set members (title is handled
// separately via volume increment).
export const COPYABLE_META_KEYS: (keyof Metadata)[] = [
  'author', 'publisher', 'published', 'description', 'tags', 'series',
  'languages', 'identifiers',
]

export type LogEntry = { time: string; level: 'info' | 'ok' | 'warn' | 'error'; msg: string }

export type ExistingBook = {
  id: string
  title: string | null
  author: string | null
  clearance: number
  locations: string[]
  cover_url?: string | null
}

export type CommittedBook = ExistingBook & { original_filename: string }

// Source-agnostic: 'local' is a browser upload (this changeset); 'server' is the
// future "import a file already under /Books/Unsorted" path. Everything after
// staging is identical for both.
export type UploadSource =
  | { kind: 'local'; file: File }
  | { kind: 'server'; path: string }

export type UploadStatus =
  | 'queued' | 'uploading' | 'staged' | 'duplicate' | 'error'
  | 'committing' | 'committed'

export interface UploadItem {
  localId: string
  source: UploadSource
  status: UploadStatus
  progress: number
  // upload result
  stagingId: string | null
  hash: string | null
  format: string
  size: number
  stagingFilename: string
  // editable form
  filename: string
  meta: Metadata
  clearance: number
  needsReview: boolean
  selectedDir: string
  extraSubpath: string
  coverOverride: File | null
  stagingCoverUrl: string | null
  prefilled: boolean
  excludeFromApply: boolean   // ticked in the sidebar → skip in "Apply first to all"
  // results
  existingBook: ExistingBook | null
  committedBook: CommittedBook | null
  errorMsg: string
  log: LogEntry[]
}

// Display / volume-detection name — the original source name, stable across the
// fb2→fb2.zip re-zip the backend does on commit.
export function sourceName(s: UploadSource): string {
  return s.kind === 'local' ? s.file.name : basename(s.path)
}

// Local-only id for keying tabs. NOT crypto.randomUUID() — that's restricted to
// secure contexts, and the app is served over plain http on the LAN.
let _seq = 0
function localId(): string {
  return `u${Date.now().toString(36)}-${(_seq++).toString(36)}`
}

export function makeItem(source: UploadSource): UploadItem {
  return {
    localId: localId(),
    source,
    status: 'queued',
    progress: 0,
    stagingId: null,
    hash: null,
    format: '',
    size: source.kind === 'local' ? source.file.size : 0,
    stagingFilename: '',
    filename: '',
    meta: { ...DEFAULT_META },
    clearance: 100,
    needsReview: false,
    selectedDir: '',
    extraSubpath: '',
    coverOverride: null,
    stagingCoverUrl: null,
    prefilled: false,
    excludeFromApply: false,
    existingBook: null,
    committedBook: null,
    errorMsg: '',
    log: [],
  }
}
