// node serve.mjs <dir> <port>: serves a client build (or this folder's test pages) and proxies /api, /track and
// /ws to the backend on 127.0.0.1:8000. /src/... falls back to client/src so the test pages import the live modules.
import http from 'node:http'
import net from 'node:net'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { join, extname, normalize, resolve } from 'node:path'

const SOURCE_ROOT = resolve(import.meta.dirname, '../../client/src')

const [dist, port] = [process.argv[2], Number(process.argv[3] || 4100)]
const BACKEND = { host: '127.0.0.1', port: 8000 }
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.mp3': 'audio/mpeg', '.woff2': 'font/woff2', '.wasm': 'application/wasm' }
const proxied = url => url.startsWith('/api') || url.startsWith('/track') || url.startsWith('/ws')

const server = http.createServer((req, res) => {
  if (proxied(req.url)) {
    const upstream = http.request({ ...BACKEND, method: req.method, path: req.url, headers: { ...req.headers, host: '127.0.0.1:8000' } }, up => {
      res.writeHead(up.statusCode, up.headers)
      up.pipe(res)
    })
    upstream.on('error', () => { res.writeHead(502); res.end() })
    req.pipe(upstream)
    return
  }
  const path = normalize(decodeURIComponent(req.url.split('?')[0])).replace(/^([\\/])+/, '')
  const fromSource = /^src[\\/]/.test(path) && !existsSync(join(dist, path))
  const root = fromSource ? SOURCE_ROOT : dist
  let file = join(root, fromSource ? path.slice(4) : path)
  if (!existsSync(file) && existsSync(file + '.js')) file += '.js'
  if (!file.startsWith(normalize(root)) || !existsSync(file) || statSync(file).isDirectory()) file = join(dist, 'index.html')
  if (!existsSync(file)) { res.writeHead(404); res.end(); return }
  res.writeHead(200, { 'Content-Type': TYPES[extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' })
  createReadStream(file).pipe(res)
})

server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(BACKEND.port, BACKEND.host, () => {
    const headers = Object.entries({ ...req.headers, host: '127.0.0.1:8000' }).map(([k, v]) => `${k}: ${v}`).join('\r\n')
    upstream.write(`${req.method} ${req.url} HTTP/1.1\r\n${headers}\r\n\r\n`)
    upstream.write(head)
    upstream.pipe(socket)
    socket.pipe(upstream)
  })
  upstream.on('error', () => socket.destroy())
  socket.on('error', () => upstream.destroy())
})

server.listen(port, '127.0.0.1', () => console.log(`serving ${dist} on http://127.0.0.1:${port}`))
