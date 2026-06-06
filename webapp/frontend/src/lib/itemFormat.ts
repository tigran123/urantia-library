export function formatBytes(bytes: number | null | undefined, decimals = 2): string {
  if (!bytes || !+bytes) return '0 Bytes'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

const FORMAT_LABELS: Record<string, string> = {
  pdf: 'PDF',
  djvu: 'DjVu',
  epub: 'ePub',
  txt: 'TXT',
  md: 'MD',
  markdown: 'MD',
  mobi: 'MOBI',
  azw: 'AZW',
  azw3: 'AZW3',
  html: 'HTML',
  htm: 'HTML',
  cpp: 'C++',
  mp3: 'MP3',
  wav: 'WAV',
  ogg: 'OGG',
  flac: 'FLAC',
  m4a: 'M4A',
  aac: 'AAC',
  mp4: 'MP4',
  webm: 'WebM',
  mkv: 'MKV',
  avi: 'AVI',
  mov: 'MOV',
}

const CODE_EXTS = new Set([
  'c', 'h', 'hpp', 'py', 'js', 'ts', 'jsx', 'tsx', 'lua', 'sh', 'bash',
  'rs', 'go', 'java', 'css', 'scss', 'json', 'xml', 'yaml', 'yml', 'sql', 'ini',
])

// Presentation date as "D MMM YYYY" in both locales (e.g. "26 May 2026" /
// "26 мая 2026"). The Russian " г." year suffix is stripped. Stored ISO
// timestamps are unchanged — this only formats for display.
export function formatShortDate(locale: string, iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return new Intl.DateTimeFormat(locale === 'ru' ? 'ru-RU' : 'en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
  }).format(d).replace(/\s*г\.?$/u, '')
}

// Audio formats mirror the backend `_AUDIO_EXTS` (main.py). Used to decide
// whether a directory listing can offer the Album view.
export const AUDIO_EXTS = new Set(['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac'])

export function isAudioFile(name: string): boolean {
  const m = (name || '').toLowerCase().match(/\.([^.]+)$/)
  return !!m && AUDIO_EXTS.has(m[1])
}

// mm:ss clock for a duration in seconds (player elapsed / track length).
export function formatClock(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec < 0) return '0:00'
  const s = Math.round(sec)
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

// H:MM:SS (or M:SS under an hour) for a duration in seconds — precise clock for the
// ItemView Details panel. Returns '' for unknown/invalid input.
export function formatClockLong(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec < 0) return ''
  const s = Math.round(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`
}

// Localized album runtime, e.g. "36 min" / "1 hr 4 min". Uses the i18n `t`
// from the caller so en/ru units stay in one place (i18n `album.runtime_*`).
export function formatRuntime(sec: number, t: (key: string, named?: Record<string, unknown>) => string): string {
  const totalMin = Math.round(sec / 60)
  if (totalMin < 60) return t('album.runtime_min', { n: totalMin })
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  return t('album.runtime_hr_min', { h, m })
}

export function fileTypeLabel(name: string): string | null {
  if (!name) return null
  const n = name.toLowerCase()
  if (n.endsWith('.fb2.zip') || n.endsWith('.fb2')) return 'FB2'
  if (n.endsWith('.html.zip') || n.endsWith('.htm.zip')) return 'HTML'
  const m = n.match(/\.([^.]+)$/)
  if (!m) return null
  const ext = m[1]
  if (FORMAT_LABELS[ext]) return FORMAT_LABELS[ext]
  if (CODE_EXTS.has(ext)) return ext.toUpperCase()
  return null
}
