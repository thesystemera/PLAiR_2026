import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { existsSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const PRECACHE_PUBLIC_DIRS = ['audio/interface']
const PRECACHE_PUBLIC_FILES = [
  'index.html',
  'manifest.json',
  'images/favicon.ico',
  'images/plair_icon.png',
  'images/plair_icon_192.png',
  'images/plair_icon_512.png',
  'images/plair_icon_maskable.png',
  'images/plair_icon_maskable_192.png',
  'images/apple-touch-icon.png',
  'images/default_background.webp',
  'images/default_profile_pic.png',
]

function offlineAssetManifest() {
  const publicDir = fileURLToPath(new URL('./public/', import.meta.url))
  return {
    name: 'plair-offline-asset-manifest',
    apply: 'build',
    generateBundle(_, bundle) {
      const built = Object.keys(bundle).filter(name => name.startsWith('assets/')).map(name => `/${name}`)
      const extra = PRECACHE_PUBLIC_DIRS.flatMap(dir => {
        const full = `${publicDir}${dir}`
        return existsSync(full) ? readdirSync(full).map(file => `/${dir}/${file}`) : []
      })
      const files = PRECACHE_PUBLIC_FILES.filter(file => file === 'index.html' || existsSync(`${publicDir}${file}`)).map(file => `/${file}`)
      const assets = [...new Set(['/', ...files, ...extra, ...built])]
      this.emitFile({ type: 'asset', fileName: 'asset-manifest.json', source: JSON.stringify({ assets }, null, 2) })
    },
  }
}

export default defineConfig({
  plugins: [react(), offlineAssetManifest()],
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