/// <reference types="vitest" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    // `e2e/` is Playwright's, not vitest's. Vitest's default include would
    // collect those specs and fail on `import { test } from '@playwright/test'`
    // -- two runners fighting over the same glob, which is a confusing failure
    // to read because it reports as a frontend unit-test failure.
    exclude: ['**/node_modules/**', '**/dist/**', 'e2e/**'],
  },
})
