import { existsSync } from 'node:fs'
import { defineConfig } from '@playwright/test'

const chrome =
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ??
  (process.platform === 'darwin' &&
  existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    : undefined)

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5180',
    launchOptions: { executablePath: chrome },
    viewport: { width: 1440, height: 960 },
  },
  webServer: {
    command: 'npm run dev -- --port 5180',
    url: 'http://127.0.0.1:5180',
    reuseExistingServer: false,
  },
})
