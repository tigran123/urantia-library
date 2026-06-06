import api from '../api'

// Build a full asset URL from an API-relative path (e.g. `/api/covers/<hash>`),
// honouring APP_ROOT_PATH the same way the inline `getFullUrl` helpers in the
// views do. Stripping `/api` from the axios baseURL yields the SPA root, then
// we re-append the asset path.
export function getFullUrl(url?: string | null): string {
  if (!url) return ''
  return (api.defaults.baseURL?.replace('/api', '') || '') + url
}

// Full `/api/files/...` URL for a library-relative path, encoding each path
// segment. Single source of truth for every surface that links to or streams a
// raw library file (reader, browse/search downloads, playlist items, audio player).
export function fileUrl(path: string): string {
  return getFullUrl(`/api/files/${path.split('/').map(encodeURIComponent).join('/')}`)
}
