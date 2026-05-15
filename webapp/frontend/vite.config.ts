import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ command }) => {
  // 'serve' is used by `npm run dev`
  // 'build' is used by `npm run build`
  const isDev = command === 'serve'

  return {
    plugins: [vue(), tailwindcss()],
    // Dynamically switch the base based on the environment

    base: isDev ? '/' : '/library/',

    // The proxy block is completely ignored during `npm run build`,
    // so it's safe to just leave it here permanently for local dev.
    server: {
      proxy: {
        '/library': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/library/, '')
        }
      }
    }
  }
})
