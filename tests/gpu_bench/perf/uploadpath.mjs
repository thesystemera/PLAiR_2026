// node uploadpath.mjs [rounds] - the cover renderer's upload path reproduced in a throwaway WebGL2 context on the phone:
// real cover packs (/api/artwork/<id>/pack/512) decoded and cut in a worker exactly as packImage.js does
// (createImageBitmap with premultiplyAlpha / colorSpaceConversion 'none', then a crop per half), handed to the page and
// uploaded with texImage2D as RGBA or as RGB (what the depth map uses), one cover half per frame while the Catalog is
// flung; 'raw' has the worker draw each half into a CPU canvas and hand over its pixel bytes instead (uploaded as
// RGBA from memory); 'pool' re-fills six textures allocated once up front (texStorage2D) with texSubImage2D. 'flush' is 'rgba' with gl.flush() after each
// upload. MODES picks them (default 'rgba,pool'). Prints per mode the calls, average, 95th percentile and worst blocking time. Nothing is shown.
import { page, sleep, openPanel } from './cdp.mjs'

const rounds = Number(process.argv[2] || 2)
const p = await page()
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
await openPanel(p, 'Catalog')
console.log(await p.evaluate(`(async () => {
  const list = await (await fetch('/api/catalog/tracks?skip=300&limit=40&sort_by=created_at&order=desc')).json()
  const ids = (list.tracks || []).map(t => t.id).filter(Boolean)
  const source = "self.onmessage = async e => { if (e.data && e.data.raw !== undefined) { self.raw = e.data.raw; return } const out = []; for (const url of e.data) { try { const blob = await (await fetch(url)).blob(); const packed = await createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' }); const half = Math.floor(packed.width / 2); const color = await createImageBitmap(packed, 0, 0, half, packed.height); const map = await createImageBitmap(packed, half, 0, half, packed.height); packed.close(); if (self.raw) { for (const b of [color, map]) { const c = new OffscreenCanvas(b.width, b.height); const x = c.getContext('2d', { willReadFrequently: true }); x.drawImage(b, 0, 0); const d = x.getImageData(0, 0, b.width, b.height); b.close(); out.push({ width: d.width, height: d.height, data: d.data }) } } else out.push(color, map) } catch (err) {} } self.postMessage(out, out.map(o => o.data ? o.data.buffer : o)) }"
  const worker = new Worker(URL.createObjectURL(new Blob([source], { type: 'text/javascript' })))
  window.__bitmaps = []
  window.__decode = (urls, raw) => new Promise(resolve => { worker.onmessage = e => { window.__bitmaps.push(...e.data); resolve(e.data.length) }; worker.postMessage(urls); })
  window.__setRaw = raw => worker.postMessage([])
  window.__worker = worker
  window.__packUrls = ids.map(id => location.origin + '/api/artwork/' + id + '/pack/512')
  const canvas = document.createElement('canvas')
  canvas.width = 4; canvas.height = 4
  const gl = canvas.getContext('webgl2')
  let running = false, format = 'rgba', calls = [], pool = [], poolNext = 0
  const tick = () => {
    if (!running) return
    const bitmap = window.__bitmaps.shift()
    if (bitmap && format === 'pool') {
      const texture = pool[poolNext++ % pool.length]
      gl.bindTexture(gl.TEXTURE_2D, texture)
      const start = performance.now()
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, bitmap.width, bitmap.height, gl.RGBA, gl.UNSIGNED_BYTE, bitmap)
      calls.push(performance.now() - start)
      bitmap.close()
    } else if (bitmap) {
      const texture = gl.createTexture()
      gl.bindTexture(gl.TEXTURE_2D, texture)
      const f = format === 'rgb' ? gl.RGB : gl.RGBA
      if (format === 'flush') gl.flush()
      const start = performance.now()
      if (bitmap.data) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, bitmap.width, bitmap.height, 0, gl.RGBA, gl.UNSIGNED_BYTE, bitmap.data)
      else gl.texImage2D(gl.TEXTURE_2D, 0, f, f, gl.UNSIGNED_BYTE, bitmap)
      if (format === 'flush') gl.flush()
      calls.push(performance.now() - start)
      if (bitmap.close) bitmap.close()
      gl.deleteTexture(texture)
    }
    requestAnimationFrame(tick)
  }
  window.__path = {
    start: m => {
      format = m; calls = []
      if (m === 'pool' && !pool.length) {
        const first = window.__bitmaps[0]
        for (let i = 0; i < 6; i++) { const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t); gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, first.width, first.height); pool.push(t) }
        gl.finish()
      }
      running = true; requestAnimationFrame(tick); return m
    },
    stop: () => { running = false; const c = [...calls].sort((a, b) => a - b); if (!c.length) return JSON.stringify({ calls: 0 }); const total = c.reduce((a, b) => a + b, 0); return JSON.stringify({ calls: c.length, avg: +(total / c.length).toFixed(2), p95: +c[Math.floor(c.length * 0.95)].toFixed(1), worst: +c[c.length - 1].toFixed(1) }) },
    drop: () => { running = false; worker.terminate(); for (const b of window.__bitmaps) if (b.close) b.close(); delete window.__bitmaps; gl.getExtension('WEBGL_lose_context')?.loseContext(); delete window.__path; return 1 },
  }
  return 'ready, ' + ids.length + ' packs'
})()`))
try {
  for (let r = 0; r < rounds; r++) {
    const modes = (process.env.MODES || 'rgba,pool').split(',')
    for (const [m, mode] of modes.entries()) {
      const from = ((r * modes.length + m) * 10) % 40
      const urls = JSON.parse(await p.evaluate(`JSON.stringify(window.__packUrls.slice(${from}, ${from + 10}))`))
      await p.evaluate(`window.__worker.postMessage({ raw: ${mode === 'raw'} }), 1`)
      console.log(`decoded ${await p.evaluate(`window.__decode(${JSON.stringify(urls)})`)} halves`)
      await p.evaluate(`window.__path.start('${mode}')`)
      for (const direction of [-1, 1]) {
        const res = await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * 1800, speed: 2500, gestureSourceType: 'touch' })
        if (res.error) throw new Error(res.error.message)
        await sleep(200)
      }
      console.log(`round ${r + 1} ${mode.padEnd(5)} ${await p.evaluate('window.__path.stop()')}`)
    }
  }
} finally {
  await p.evaluate('window.__path?.drop(), 1')
}
p.close()
