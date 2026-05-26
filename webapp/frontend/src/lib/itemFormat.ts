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
