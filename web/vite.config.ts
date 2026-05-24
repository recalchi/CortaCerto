/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',   // bind to IPv4 loopback — avoids localhost→::1 on Windows
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:7472', changeOrigin: true },
      '/ws':  { target: 'ws://127.0.0.1:7472',  ws: true },
    },
  },
  test: {
    environment: 'node',           // store tests don't need a DOM
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    globals: false,
  },
})
