import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: '0.0.0.0',
    allowedHosts: ['www.plair.live', 'plair.live'],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
      '/track': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
    hmr: {
      overlay: true,
    },
    headers: {
      'Cache-Control': 'no-store, no-cache, must-revalidate',
      'Pragma': 'no-cache',
      'Expires': '0',
    },
  },
  build: {
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (/node_modules[\\/](three|@react-three[\\/]fiber)[\\/]/.test(id)) return 'vendor-three'
          if (/node_modules[\\/](framer-motion|motion-dom|motion-utils)[\\/]/.test(id)) return 'vendor-motion'
          if (/node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'vendor-react'
          return undefined
        },
      },
    },
  },
})