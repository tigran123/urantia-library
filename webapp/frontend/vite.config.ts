import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { promises as fs } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

// Expose pdfjs-dist's bundled CMaps and Foxit standard-font substitutes at
// <base>/pdfjs/. PdfViewer.vue points pdfjsLib.getDocument({ cMapUrl,
// standardFontDataUrl }) here so PDFs that reference unembedded Base-14 fonts
// (Acrobat Paper-Capture facsimile scans, in particular) render past the
// cover page, and so CJK/CID-encoded PDFs can resolve their character maps.
const pdfjsAssets = (): Plugin => {
  const pdfjsDir = resolve(__dirname, 'node_modules/pdfjs-dist')
  const sources = ['cmaps', 'standard_fonts', 'wasm']
  return {
    name: 'pdfjs-assets',
    configureServer(server) {
      const base = server.config.base
      server.middlewares.use(async (req, res, next) => {
        const path = req.url?.split('?')[0]
        if (!path) return next()
        for (const sub of sources) {
          const prefix = `${base}pdfjs/${sub}/`
          if (path.startsWith(prefix)) {
            try {
              res.end(await fs.readFile(resolve(pdfjsDir, sub, path.slice(prefix.length))))
              return
            } catch { /* fall through to next() */ }
          }
        }
        next()
      })
    },
    async closeBundle() {
      const out = resolve(__dirname, 'dist/pdfjs')
      for (const sub of sources) {
        await fs.cp(join(pdfjsDir, sub), join(out, sub), { recursive: true })
      }
    },
  }
}

// Vite 8 does NOT normalize import.meta.env.BASE_URL to end in a slash,
// even though it normalizes the value used for its own asset URLs in
// index.html. So user code that does `${BASE_URL}foo` breaks silently when
// APP_ROOT_PATH is set without a trailing slash (e.g. `/library` →
// `/librarypdfjs/...`). Force the slash here so every consumer sees the
// same shape.
const APP_BASE = (process.env.APP_ROOT_PATH || '/').replace(/\/?$/, '/')

// https://vite.dev/config/
export default defineConfig({
  base: APP_BASE,
  plugins: [
    vue(),
    tailwindcss(),
    pdfjsAssets(),
  ],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
      }
    }
  }
})
