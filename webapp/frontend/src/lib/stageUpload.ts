// Uploading a file to the admin staging area.
//
// `POST /admin/books/upload` answers with an SSE stream rather than JSON: the
// server narrates hashing / metadata extraction / cover extraction as it goes
// (`log` events) and finishes with a single `done` event carrying the staging
// record. That needs raw `fetch` — axios buffers the whole body, so a progress
// log arriving over a 850 MB upload would only surface once it was all over.
//
// Two callers stage a local file this way: the Add-Book wizard
// (AdminUploadView) and the per-book "Replace file…" action
// (BookMetadataEditor), so the stream reader lives here rather than in either.

import api from '../api'

/** One `log` event, as emitted by admin_uploads._log_entry. */
export type StageLogEntry = { time: string; level: 'info' | 'ok' | 'warn' | 'error'; msg: string }

/** The `done` payload. Exactly one of these three shapes arrives:
 *  a staged record, `existing` (the bytes are already a book), or `error`. */
export type StagedPayload = {
  staging_id?: string
  hash?: string
  size?: number
  format?: string
  filename?: string
  cover_url?: string | null
  extracted_metadata?: Record<string, any>
  existing?: any
  error?: string
}

export const stageLocalFile = async (
  file: File,
  opts: { signal?: AbortSignal; onLog?: (entry: StageLogEntry) => void } = {},
): Promise<StagedPayload> => {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(`${api.defaults.baseURL ?? '/api'}/admin/books/upload`, {
    method: 'POST', body: form, credentials: 'include', signal: opts.signal,
  })
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => '')
    throw new Error(text || `HTTP ${resp.status}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let done: StagedPayload | null = null

  const handleEvent = (raw: string) => {
    let eventName = 'message'
    let dataStr = ''
    for (const line of raw.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim()
      else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
    }
    if (!dataStr) return
    let payload: any
    try { payload = JSON.parse(dataStr) } catch { return }
    if (eventName === 'log') opts.onLog?.(payload)
    else if (eventName === 'done') done = payload
  }

  while (true) {
    const { value, done: streamDone } = await reader.read()
    if (streamDone) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      handleEvent(buf.slice(0, idx))
      buf = buf.slice(idx + 2)
    }
  }

  // A stream that ends without `done` means the server died mid-upload. Left
  // unreported it would strand the caller in a permanent "uploading" state.
  if (!done) throw new Error('Upload ended without a result')
  return done
}
