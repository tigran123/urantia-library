<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'
import welcomeEn from '../welcome/welcome-en.md?raw'
import welcomeRu from '../welcome/welcome-ru.md?raw'

// Welcome / introduction content. Like the legal docs (PrivacyView/TermsView)
// it is bundled into the SPA via ?raw so it lives in — and evolves with — the
// source tree, rather than being served as a file from the library root.
const { locale } = useI18n({ useScope: 'global' })

const html = computed(() => {
  const source = locale.value === 'ru' ? welcomeRu : welcomeEn
  return marked.parse(source, { breaks: false, gfm: true })
})
</script>

<template>
  <article class="legal-doc max-w-3xl mx-auto px-6 py-8 text-gray-800 dark:text-gray-200" v-html="html" />
</template>

<style scoped>
.legal-doc :deep(h1) { font-size: 1.9rem; font-weight: 700; margin: 0 0 1rem; }
.legal-doc :deep(h2) { font-size: 1.35rem; font-weight: 600; margin: 1.6rem 0 0.6rem; }
.legal-doc :deep(h3) { font-size: 1.1rem; font-weight: 600; margin: 1.2rem 0 0.4rem; }
.legal-doc :deep(p)  { margin: 0 0 0.85rem; line-height: 1.6; }
.legal-doc :deep(em) { color: rgb(107 114 128); font-style: italic; }
.legal-doc :deep(ul),
.legal-doc :deep(ol) { margin: 0 0 0.85rem 1.5rem; padding: 0; }
.legal-doc :deep(ul) { list-style: disc; }
.legal-doc :deep(ol) { list-style: decimal; }
.legal-doc :deep(li) { margin: 0.2rem 0; line-height: 1.55; }
.legal-doc :deep(a)  { color: rgb(37 99 235); text-decoration: underline; }
.dark .legal-doc :deep(a) { color: rgb(96 165 250); }
.legal-doc :deep(strong) { font-weight: 600; }
.legal-doc :deep(code) {
  background: rgba(127, 127, 127, 0.15);
  padding: 0.1em 0.35em;
  border-radius: 3px;
  font-family: "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 0.92em;
}
</style>
