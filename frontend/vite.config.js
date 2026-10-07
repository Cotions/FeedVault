import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import { createRequire } from 'node:module'

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

// Everything a page loads comes from FeedVault itself (#148): the fonts
// once came from Google Fonts, which saw every page load and failed
// offline. What an HTML page or a style sheet points another origin at:
// a <link> or <script> to http(s):// or //, an @import or a url() of one
// (a data: URL is the page's own, even with an SVG's xmlns inside).
const EXTERNAL = /^\s*(?:https?:)?\/\//i
const unquote = s => s.trim().replace(/^(['"])(.*)\1$/s, '$2')

export function externalRefs(text, kind) {
  const found = []
  if (kind === 'html') {
    for (const [tag] of text.matchAll(/<(?:link|script)\b[^>]*>/gi)) {
      for (const [, , value] of tag.matchAll(/\b(href|src)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi)) {
        if (EXTERNAL.test(unquote(value))) found.push(unquote(value))
      }
    }
    for (const [, css] of text.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) found.push(...externalRefs(css, 'css'))
    for (const [, , css] of text.matchAll(/\bstyle\s*=\s*(["'])([\s\S]*?)\1/gi)) found.push(...externalRefs(css, 'css'))
    return found
  }
  for (const [, value] of text.matchAll(/url\(\s*("[^"]*"|'[^']*'|[^)]*)\)/gi)) {
    if (EXTERNAL.test(unquote(value))) found.push(unquote(value))
  }
  for (const [, value] of text.matchAll(/@import\s+("[^"]*"|'[^']*')/gi)) {
    if (EXTERNAL.test(unquote(value))) found.push(unquote(value))
  }
  return found
}

// The build fails if dist's HTML or CSS points anywhere else, and it ships
// the fonts' SIL OFL licences next to them (dist/assets/fonts-*.LICENSE.txt).
const require = createRequire(import.meta.url)
const FONT_LICENCES = {
  'fonts-bricolage-grotesque.LICENSE.txt': '@fontsource-variable/bricolage-grotesque/LICENSE',
  'fonts-jetbrains-mono.LICENSE.txt': '@fontsource/jetbrains-mono/LICENSE',
}

export function selfHosted() {
  return {
    name: 'feedvault-self-hosted',
    apply: 'build',
    enforce: 'post',
    generateBundle(_, bundle) {
      for (const [name, file] of Object.entries(FONT_LICENCES)) {
        this.emitFile({ type: 'asset', fileName: `assets/${name}`, source: fs.readFileSync(require.resolve(file)) })
      }
      const bad = []
      for (const out of Object.values(bundle)) {
        const kind = out.fileName.endsWith('.html') ? 'html' : out.fileName.endsWith('.css') ? 'css' : null
        if (!kind || out.type !== 'asset') continue
        for (const ref of externalRefs(String(out.source), kind)) bad.push(`${out.fileName}: ${ref}`)
      }
      if (bad.length) {
        this.error(`another origin in the built UI (serve it from FeedVault instead, #148):\n  ${bad.join('\n  ')}`)
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), selfHosted()],
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
