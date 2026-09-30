import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend (127.0.0.1:3380) serves the built dashboard itself, so the app
// always talks to its own origin. In dev the Vite server forwards /api and
// /media there, which keeps dev same-origin too: no CORS, and the X-FeedVault
// header check behaves exactly as in production.
const BACKEND = 'http://127.0.0.1:3380'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api':   { target: BACKEND, changeOrigin: true },
      '/media': { target: BACKEND, changeOrigin: true },
    },
  },
  build: { outDir: 'dist' },
})
