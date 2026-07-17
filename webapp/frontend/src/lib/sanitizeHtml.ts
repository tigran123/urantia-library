import DOMPurify from 'dompurify'

// Book descriptions arrive as HTML (calibre comments, FB2 <annotation>,
// .htaccess AddDescription lines, admin edits) and several views render them
// with v-html. Only admins can write the field, but the markup itself is
// third-party — embedded metadata travels with the ebook file — so it must
// never reach v-html unsanitized. Strict allowlist: basic text formatting
// plus http(s) links, nothing else (no images, no styles, no event handlers).
const ALLOWED_TAGS = [
  'p', 'br', 'div', 'span', 'blockquote',
  'b', 'strong', 'i', 'em', 'u', 's', 'sub', 'sup',
  'ul', 'ol', 'li', 'a',
]

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  // Surviving links open in a new tab without a window.opener handle.
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

export function sanitizeDescription(html: string | null | undefined): string {
  if (!html) return ''
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: ['href'],
    ALLOWED_URI_REGEXP: /^https?:\/\//i,
  })
}
