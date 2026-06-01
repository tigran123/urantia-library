// Shared source type for built-in viewers. Lets the same components render
// either a committed library file (path + hashId) or a staged upload that
// hasn't been committed yet (stagingId).

export type ViewerSource =
  | { kind: 'live'; path: string; hashId: string }
  | { kind: 'staging'; stagingId: string; filename: string }

const enc = (p: string) => p.split('/').map(encodeURIComponent).join('/')

// All returned URLs are relative to the api wrapper's baseURL ('/api').
export const viewerUrls = (s: ViewerSource) => {
  if (s.kind === 'staging') {
    const base = `/admin/books/upload/${encodeURIComponent(s.stagingId)}`
    return {
      file: `${base}/file`,
      fb2Content: `${base}/fb2-content`,
      mdContent: `${base}/md-content`,
      htmlContent: `${base}/html-content`,
      djvuMeta: `${base}/djvu-metadata`,
      djvuPage: `${base}/djvu-page`,
      djvuOutline: `${base}/djvu-outline`,
    }
  }
  return {
    file: `/files/${enc(s.path)}`,
    fb2Content: '/fb2-content',
    mdContent: '/md-content',
    htmlContent: '/html-content',
    djvuMeta: '/djvu-metadata',
    djvuPage: '/djvu-page',
    djvuOutline: '/djvu-outline',
  }
}

// Live endpoints take ?path=... as a query param; staging endpoints embed the
// staging_id in the route, so query params only carry per-call extras (page, etc.).
export const viewerParams = (s: ViewerSource, extra?: Record<string, any>): Record<string, any> =>
  s.kind === 'staging' ? { ...(extra || {}) } : { path: s.path, ...(extra || {}) }

// hashId is only known for committed books; staging previews can't save reading
// progress, so progress endpoints become no-ops when this returns null.
export const sourceHashId = (s: ViewerSource): string | null =>
  s.kind === 'live' ? s.hashId : null

// The identity of the bytes a viewer should fetch — a change here (and only
// here) warrants a reload. In the upload UI the filename is editable and varies
// independently: it's sniffed for UI variants (sourceFilename) but never changes
// which bytes the staging endpoints serve, so it's deliberately excluded here.
export const sourceLoadKey = (s: ViewerSource): string =>
  s.kind === 'staging' ? `staging:${s.stagingId}` : `live:${s.path}`

// Basename for extension sniffing inside viewers that pick UI variants by file
// suffix (e.g. MdViewer hiding its TOC pane for .txt).
export const sourceFilename = (s: ViewerSource): string =>
  s.kind === 'staging' ? s.filename : (s.path.split('/').pop() || '')
