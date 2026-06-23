// Cross-platform hyphenation by injecting U+00AD (soft hyphen) into text nodes.
//
// Native CSS `hyphens: auto` does nothing on Android Chrome — it has no bundled
// hyphenation dictionaries (desktop Chrome fetches them via the component
// updater; Android does not). But *every* browser breaks a line at a soft
// hyphen, so we insert them ourselves at syllable boundaries with the `hyphen`
// library and let CSS `hyphens: manual` break only there. The result is then
// identical on every platform.
//
// Anchoring: the FB2/HTML annotation layer (lib/anchors/html.ts) treats U+00AD
// as zero-width when computing character offsets, so the soft hyphens injected
// here are invisible to its anchoring math. EPUB CFIs are computed inside
// epub.js and can't be made shy-agnostic — new annotations round-trip fine,
// pre-existing ones may drift up to ~1 word.

export const SOFT_HYPHEN = '­'

export type HyphenateFn = (text: string) => string

// Languages we ship patterns for. A book whose language isn't listed here is
// left unhyphenated (safe — no risk of wrong break points). `en` maps to the
// US patterns; regional variants collapse to their primary subtag.
const LANG_KEYS = ['ru', 'en', 'de', 'fr', 'es', 'it'] as const
export type LangKey = (typeof LANG_KEYS)[number]

// Literal dynamic imports so Vite code-splits each language (and its pattern
// table) into its own lazily-loaded chunk — only the book's language downloads.
const LOADERS: Record<LangKey, () => Promise<any>> = {
  ru: () => import('hyphen/ru'),
  en: () => import('hyphen/en-us'),
  de: () => import('hyphen/de'),
  fr: () => import('hyphen/fr'),
  es: () => import('hyphen/es'),
  it: () => import('hyphen/it'),
}

// Map a BCP-47-ish language tag ('ru', 'ru-RU', 'en-US', …) to a supported
// key, or null when we don't hyphenate that language.
export const langToKey = (lang: string | null | undefined): LangKey | null => {
  const primary = (lang || '').toLowerCase().split(/[-_]/)[0]
  return (LANG_KEYS as readonly string[]).includes(primary) ? (primary as LangKey) : null
}

const cache = new Map<LangKey, Promise<HyphenateFn | null>>()

// Resolve (and cache) the synchronous hyphenator for a language tag, or null
// for unsupported/unknown languages. `hyphen` is CommonJS, so the loaded module
// exposes its functions either as named exports or under `.default` depending
// on the bundler's interop — handle both.
export const getHyphenator = (lang: string | null | undefined): Promise<HyphenateFn | null> => {
  const key = langToKey(lang)
  if (!key) return Promise.resolve(null)
  let p = cache.get(key)
  if (!p) {
    p = LOADERS[key]()
      .then((mod: any) => {
        const fn = mod?.hyphenateSync ?? mod?.default?.hyphenateSync
        return typeof fn === 'function' ? ((t: string) => fn(t) as string) : null
      })
      .catch(() => null)
    cache.set(key, p)
  }
  return p
}

// Elements whose text content must never be hyphenated.
const SKIP_TAGS = new Set(['CODE', 'PRE', 'KBD', 'SAMP', 'SCRIPT', 'STYLE', 'TEXTAREA'])

const hasLetter = /\p{L}/u

// Collect eligible text nodes under `root`: must contain a letter, must not
// already contain a soft hyphen (keeps the pass idempotent), and must not live
// inside a skipped element.
const collectTextNodes = (root: HTMLElement): Text[] => {
  const out: Text[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const t = n as Text
      if (!t.data || !hasLetter.test(t.data)) return NodeFilter.FILTER_REJECT
      if (t.data.includes(SOFT_HYPHEN)) return NodeFilter.FILTER_REJECT
      let p = t.parentElement
      while (p && p !== root) {
        if (SKIP_TAGS.has(p.tagName)) return NodeFilter.FILTER_REJECT
        p = p.parentElement
      }
      return NodeFilter.FILTER_ACCEPT
    },
  })
  let n = walker.nextNode()
  while (n) {
    out.push(n as Text)
    n = walker.nextNode()
  }
  return out
}

// Synchronous: hyphenate every eligible text node under `root`. Used for the
// EPUB content hook, where the section is bounded in size and must be
// hyphenated before epub.js measures/paints it.
export const hyphenateElementWith = (root: HTMLElement, hyphenate: HyphenateFn): void => {
  for (const node of collectTextNodes(root)) {
    const next = hyphenate(node.data)
    if (next !== node.data) node.data = next
  }
}

// Async, time-sliced. Loads the hyphenator for `lang`, then processes text
// nodes in ≤8ms idle slices so a whole-book FB2 render never blocks the main
// thread. Resolves true when hyphenation was applied (language supported),
// false otherwise.
export const hyphenateElement = async (
  root: HTMLElement,
  lang: string | null | undefined,
): Promise<boolean> => {
  const hyphenate = await getHyphenator(lang)
  if (!hyphenate) return false
  const nodes = collectTextNodes(root)
  if (!nodes.length) return true

  const idle: (cb: () => void) => void =
    typeof (window as any).requestIdleCallback === 'function'
      ? (cb) => (window as any).requestIdleCallback(() => cb(), { timeout: 100 })
      : (cb) => window.setTimeout(cb, 0)

  await new Promise<void>((resolve) => {
    let i = 0
    const step = () => {
      const start = performance.now()
      while (i < nodes.length && performance.now() - start < 8) {
        const node = nodes[i++]
        const next = hyphenate(node.data)
        if (next !== node.data) node.data = next
      }
      if (i < nodes.length) idle(step)
      else resolve()
    }
    idle(step)
  })
  return true
}
