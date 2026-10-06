import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'
import { runMiddleware } from './server/runs.mjs'

const directory = fileURLToPath(new URL('.', import.meta.url))
const runsRoot = process.env.EBOOK_RUNS_DIR
  ? resolve(process.env.EBOOK_RUNS_DIR)
  : resolve(directory, '../.ebook-runs')

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'diorama-runs',
      configureServer(server) {
        server.middlewares.use(runMiddleware(runsRoot))
      },
      configurePreviewServer(server) {
        server.middlewares.use(runMiddleware(runsRoot))
      },
    },
  ],
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
  test: { environment: 'jsdom', include: ['tests/**/*.test.ts'] },
})
