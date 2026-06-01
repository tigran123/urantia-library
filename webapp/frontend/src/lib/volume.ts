// Pure helpers for detecting multi-volume sets from filenames and for
// volume-incrementing a title when batch-uploading a series (e.g. tom1.pdf +
// "…Том 1" → tom2.pdf prefilled to "…Том 2"). No Vue / DOM dependencies, so the
// same logic works for browser File names and (future) server-path basenames.

// Multi-suffix extensions handled as a single unit (mirrors the backend's
// _effective_suffix / _read_*_bytes handling).
const MULTI_SUFFIXES = [
  '.fb2.zip', '.txt.zip', '.md.zip', '.markdown.zip', '.html.zip', '.htm.zip',
]

export function basename(path: string): string {
  const parts = path.split(/[\\/]/)
  return parts[parts.length - 1] || path
}

export function stripExt(name: string): string {
  const lower = name.toLowerCase()
  for (const suf of MULTI_SUFFIXES) {
    if (lower.endsWith(suf)) return name.slice(0, name.length - suf.length)
  }
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(0, i) : name
}

// ---- roman numerals --------------------------------------------------------

const ROMAN_MAP: Record<string, number> = { i: 1, v: 5, x: 10, l: 50, c: 100, d: 500, m: 1000 }

export function intToRoman(n: number): string {
  if (n <= 0 || n >= 4000) return String(n)
  const table: [number, string][] = [
    [1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'],
    [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I'],
  ]
  let res = ''
  let r = n
  for (const [val, sym] of table) {
    while (r >= val) { res += sym; r -= val }
  }
  return res
}

export function romanToInt(s: string): number | null {
  if (!/^[ivxlcdm]+$/i.test(s)) return null
  const t = s.toLowerCase()
  let total = 0
  for (let i = 0; i < t.length; i++) {
    const cur = ROMAN_MAP[t[i]]
    const next = ROMAN_MAP[t[i + 1]]
    if (next && cur < next) total -= cur
    else total += cur
  }
  // Round-trip so gibberish like "iiii"/"vv" is rejected.
  return intToRoman(total).toLowerCase() === t ? total : null
}

// ---- volume token detection in a filename ----------------------------------

export interface VolumeInfo {
  keyword: string | null  // matched keyword (lowercased) or null for a bare number
  num: number
  raw: string             // raw matched number text, e.g. '01' or 'II'
  start: number           // index of the number within the ext-stripped stem
  end: number
  pad: number             // zero-pad width (0 when not padded / roman)
  roman: boolean
}

// Longest-first so 'volume' wins over 'vol' over 'v'. Single-letter keywords
// only match at a word boundary (see leading group) to avoid e.g. the 'v1' in
// "Chekhov1".
const KEYWORDS = [
  'volume', 'выпуск', 'часть', 'книга', 'book', 'part', 'tom', 'vol', 'том',
  'вып', 'кн', 'bk', 'pt', 't', 'v', 'т', 'ч',
].sort((a, b) => b.length - a.length)

const FILE_KW_SRC = `(?:^|[\\s._-])(${KEYWORDS.join('|')})[\\s._-]*(\\d+|[ivxlcdm]+)\\b`
const TRAILING_NUM_RE = /(\d+)(?=\D*$)/

function makeInfo(keyword: string | null, raw: string, start: number, end: number): VolumeInfo | null {
  const roman = !/\d/.test(raw)
  let num: number
  let pad = 0
  if (roman) {
    const r = romanToInt(raw)
    if (r == null) return null
    num = r
  } else {
    num = parseInt(raw, 10)
    pad = raw.length > String(num).length ? raw.length : 0
  }
  return { keyword: keyword ? keyword.toLowerCase() : null, num, raw, start, end, pad, roman }
}

export function detectVolume(name: string): VolumeInfo | null {
  const stem = stripExt(basename(name))
  const re = new RegExp(FILE_KW_SRC, 'gi')
  let last: RegExpExecArray | null = null
  let m: RegExpExecArray | null
  while ((m = re.exec(stem)) !== null) {
    last = m
    if (m.index === re.lastIndex) re.lastIndex++ // guard against zero-width loops
  }
  if (last) {
    const rawNum = last[2]
    const end = last.index + last[0].length
    return makeInfo(last[1], rawNum, end - rawNum.length, end)
  }
  const tm = TRAILING_NUM_RE.exec(stem)
  if (tm) return makeInfo(null, tm[1], tm.index, tm.index + tm[1].length)
  return null
}

// Stable identity of a set: the ext-stripped, lowercased stem with the volume
// run blanked out. Two files are in the same set iff their signatures are equal
// and both have a detected number.
export function setSignature(name: string): string | null {
  const info = detectVolume(name)
  if (!info) return null
  const stem = stripExt(basename(name)).toLowerCase()
  return stem.slice(0, info.start) + '\x00' + stem.slice(info.end)
}

export function sameSet(a: string, b: string): boolean {
  const sa = setSignature(a)
  return sa !== null && sa === setSignature(b)
}

// Numeric-aware filename sort so vol1 < vol2 < … < vol10.
export function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
}

// ---- title volume increment ------------------------------------------------

// Keyword markers as they appear in a *title* (display form). Longest-first.
const TITLE_KW_SRC =
  `(Volume|Выпуск|Часть|Книга|Book|Part|Vol\\.?|Том|Вып\\.?|Кн\\.?|Т\\.|Ч\\.)` +
  `\\s*([0-9]+|[ivxlcdm]+)`

function formatLike(sample: string, n: number): string {
  if (!/[0-9]/.test(sample)) {
    const roman = intToRoman(n)
    return sample === sample.toLowerCase() ? roman.toLowerCase() : roman
  }
  const s = String(n)
  return sample.length > s.length ? s.padStart(sample.length, '0') : s
}

// Replace the volume number in `title` (which equals `fromNum`) with `toNum`,
// preserving the title's own formatting. Conservative: only acts on a
// keyword-anchored marker, else a standalone equal number; otherwise returns the
// title unchanged so we never guess wrong. Always just a *default* for the user.
export function incrementTitle(title: string, fromNum: number, toNum: number): string {
  if (!title || fromNum === toNum) return title

  const kwRe = new RegExp(TITLE_KW_SRC, 'gi')
  let m: RegExpExecArray | null
  while ((m = kwRe.exec(title)) !== null) {
    const numText = m[2]
    const val = /[0-9]/.test(numText) ? parseInt(numText, 10) : romanToInt(numText)
    if (val === fromNum) {
      const numStart = m.index + m[0].length - numText.length
      return title.slice(0, numStart) + formatLike(numText, toNum) + title.slice(numStart + numText.length)
    }
    if (m.index === kwRe.lastIndex) kwRe.lastIndex++
  }

  // Standalone number equal to fromNum, bounded by non-digits (avoids the "10"
  // in "в 10 томах"). No lookbehind, for older-Safari safety.
  const bare = new RegExp(`(^|[^0-9])(${fromNum})([^0-9]|$)`).exec(title)
  if (bare) {
    const idx = bare.index + bare[1].length
    return title.slice(0, idx) + String(toNum) + title.slice(idx + bare[2].length)
  }
  return title
}
