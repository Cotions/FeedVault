import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend (127.0.0.1:3380) serves the built dashboard itself, so the app
// always talks to its own origin. In dev the Vite server forwards /api,
// /media, trash thumbnails and the userscript there, which keeps dev
// same-origin too: no CORS, and the X-FeedVault header check behaves exactly
// as in production.
const BACKEND = 'http://127.0.0.1:3380'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // No CORS: Vite's default answers any localhost origin, so a page on
    // another localhost port could call the proxied /api (changeOrigin
    // rewrites Host). And always this port, never another one.
    cors: false,
    strictPort: true,
    proxy: {
      '/api':   { target: BACKEND, changeOrigin: true },
      '/media': { target: BACKEND, changeOrigin: true },
      // Trash thumbnails only: /trash itself is a dashboard page.
      '^/trash/[0-9a-f]+/thumb': { target: BACKEND, changeOrigin: true },
      // The userscript Settings › About links to.
      '/userscript': { target: BACKEND, changeOrigin: true },
    },
  },
  build: { outDir: 'dist' },
})
