export interface DisplayUser {
  email?: string
  real_name?: string | null
}

/**
 * 1-2 uppercase initials for the avatar fallback shown when a user has not
 * uploaded a profile picture. Derived from the real name when set
 * ("Eleonora Aivazian" → "EA"), otherwise from the email local-part
 * ("eleonora.aivazian@…" → "EA", "tigran@…" → "T").
 */
export function userInitials(user: DisplayUser | null | undefined): string {
  if (!user) return '?'
  const source = (user.real_name && user.real_name.trim())
    ? user.real_name.trim()
    : (user.email || '').split('@')[0]
  const words = source.split(/[\s._+-]+/).filter(Boolean)
  if (words.length === 0) return '?'
  const first = words[0][0]
  const last = words.length > 1 ? words[words.length - 1][0] : ''
  return (first + last).toUpperCase()
}
