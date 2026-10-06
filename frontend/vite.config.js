import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend (127.0.0.1, port FEEDVAULT_PORT, 3380 by default) serves the
// built dashboard itself, so the app always talks to its own origin. In dev
// the Vite server forwards /api, /media, trash thumbnails and the userscript
// there, which keeps dev same-origin too: no CORS, and the X-FeedVault header
// check behaves exactly as in production.
//
// The port is the backend's own (#100): `./run.sh --dev` exports the one it
// starts the backend on. A set but invalid value stops Vite rather than
// falling back to 3380, which may be the live app.
export function backendPort(value = process.env.FEEDVAULT_PORT) {
  if (value === undefined) return 3380
  const port = /^[0-9]{1,5}$/.test(value) ? Number(value) : NaN
  if (!(port >= 1 && port <= 65535)) {
    throw new Error(`FEEDVAULT_PORT must be a port number from 1 to 65535, not ${JSON.stringify(value)}`)
  }
  return port
}

const BACKEND = `http://127.0.0.1:${backendPort()}`

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
