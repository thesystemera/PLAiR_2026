// node uploadstrips.mjs [rounds] - does uploading a texture in strips stop the page waiting on Chrome's GPU process? In a
// throwaway WebGL2 context on the phone's page, while the Catalog is flung, each frame uploads 1 MB (a 512x512 RGBA
// texture) either as one texSubImage2D call or as strips of STRIP rows (default 64) spread over frames, from a
// Uint8Array or from an ImageBitmap. Prints per mode the calls, their average and worst blocking time, and the page's
// worst frame. Nothing is shown; the context is dropped at the end.
import { page, sleep, openPanel } from './cdp.mjs'

const rounds = Number(process.argv[2] || 2)
const strip = Number(process.env.STRIP || 64)
const p = await page()
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
await openPanel(p, 'Catalog')
console.log(await p.evaluate(`(async () => {
  const canvas = document.createElement('canvas')
  canvas.width = 4; canvas.height = 4
  const gl = canvas.getContext('webgl2')
  const S = 512
  const data = new Uint8Array(S * S * 4)
  for (let i = 0; i < data.length; i++) data[i] = (i * 31) & 255
  const bitmap = await createImageBitmap(new ImageData(new Uint8ClampedArray(data.buffer.slice(0)), S, S))
  const textures = Array.from({ length: 8 }, () => { const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t); gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, S, S); return t })
  let running = false, mode = 'whole', frames = [], calls = [], last = 0, next = 0, row = 0
  const tick = now => {
    if (!running) return
    if (last) frames.push(now - last)
    last = now
    const texture = textures[next % textures.length]
    gl.bindTexture(gl.TEXTURE_2D, texture)
    const rowsThisFrame = mode.startsWith('whole') ? S : ${strip}
    const start = performance.now()
    if (mode.endsWith('bitmap')) {
      gl.pixelStorei(gl.UNPACK_SKIP_ROWS, row)
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, row, S, rowsThisFrame, gl.RGBA, gl.UNSIGNED_BYTE, bitmap)
      gl.pixelStorei(gl.UNPACK_SKIP_ROWS, 0)
    } else {
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, row, S, rowsThisFrame, gl.RGBA, gl.UNSIGNED_BYTE, data, row * S * 4)
    }
    gl.flush()
    calls.push(performance.now() - start)
    row += rowsThisFrame
    if (row >= S) { row = 0; next++ }
    requestAnimationFrame(tick)
  }
  window.__strips = {
    start: m => { mode = m; frames = []; calls = []; last = 0; row = 0; running = true; requestAnimationFrame(tick); return m },
    stop: () => { running = false; const c = [...calls].sort((a, b) => a - b); const total = c.reduce((a, b) => a + b, 0); return JSON.stringify({ calls: c.length, avg: +(total / c.length).toFixed(2), p95: +c[Math.floor(c.length * 0.95)].toFixed(1), worst: +c[c.length - 1].toFixed(1), worstFrame: Math.round(Math.max(...frames)) }) },
    drop: () => { running = false; gl.getExtension('WEBGL_lose_context')?.loseContext(); delete window.__strips; return 1 },
  }
  return 'ready'
})()`))
const results = {}
try {
  for (let r = 0; r < rounds; r++) {
    for (const mode of ['whole', 'strips', 'whole bitmap', 'strips bitmap']) {
      await p.evaluate(`window.__strips.start('${mode}')`)
      for (const direction of [-1, 1]) {
        const res = await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * 1800, speed: 2500, gestureSourceType: 'touch' })
        if (res.error) throw new Error(res.error.message)
        await sleep(200)
      }
      const out = JSON.parse(await p.evaluate('window.__strips.stop()'))
      ;(results[mode] ||= []).push(out)
      console.log(`round ${r + 1} ${mode.padEnd(14)} ${JSON.stringify(out)}`)
      await sleep(500)
    }
  }
} finally {
  await p.evaluate('window.__strips?.drop(), 1')
}
p.close()
