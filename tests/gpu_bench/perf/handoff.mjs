// node handoff.mjs [rounds] [seconds] [covers] [modes] - how a cover-sized WebGL picture reaches its card, timed on the
// phone: 'copy' draws every cover into one WebGL canvas and copies each card's region into the card's own 2D canvas
// (today's path), 'handoff' draws each cover into a WebGL OffscreenCanvas and hands the finished frame to the card's
// 'bitmaprenderer' canvas with transferToImageBitmap (no paint job), 'draw' only draws (the baseline). Every card
// changes every frame, like covers in a fling. The app's own covers and background are paused for the run; test
// canvases sit over the Catalog while it runs and are removed at the end. 'one' shows the single WebGL canvas itself
// with all covers in it (one changing layer instead of one per card). Interleaved rounds, medians.
import { page, sleep } from './cdp.mjs'

const rounds = Number(process.argv[2] || 3)
const seconds = Number(process.argv[3] || 4)
const covers = Number(process.argv[4] || 8)
const modes = (process.argv[5] || 'copy,handoff,one,draw').split(',')

const p = await page()
const setup = await p.evaluate(`(() => {
  document.getElementById('handoff-test')?.remove()
  window.__plairArt.set({ skipDraw: true })
  window.__plairScene.set({ skip: true })
  const N = ${covers}, S = 446, dpr = devicePixelRatio
  const host = document.createElement('div')
  host.id = 'handoff-test'
  host.style.cssText = 'position:fixed;left:8px;top:90px;z-index:99999;display:grid;grid-template-columns:repeat(2,' + (S / dpr) + 'px);gap:6px;pointer-events:none'
  document.body.appendChild(host)
  const VS = '#version 300 es\\nin vec2 p; out vec2 uv; void main() { uv = p * 0.5 + 0.5; gl_Position = vec4(p, 0.0, 1.0); }'
  const FS = '#version 300 es\\nprecision highp float; in vec2 uv; uniform float t; uniform float k; out vec4 o;' +
    'void main() { vec2 q = uv * 6.0 + vec2(sin(t + k), cos(t * 0.7 + k)); float v = sin(q.x * 3.1 + t) * cos(q.y * 2.3 - t) + sin(length(q - 3.0) * 4.0 - t * 2.0);' +
    ' o = vec4(0.5 + 0.5 * sin(v + k), 0.5 + 0.5 * sin(v * 1.3 + 2.0), 0.5 + 0.5 * cos(v + k * 0.5), 1.0); }'
  const program = gl => {
    const make = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s }
    const pr = gl.createProgram()
    gl.attachShader(pr, make(gl.VERTEX_SHADER, VS)); gl.attachShader(pr, make(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(pr); gl.useProgram(pr)
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW)
    const loc = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
    return { t: gl.getUniformLocation(pr, 't'), k: gl.getUniformLocation(pr, 'k') }
  }
  const cards = kind => Array.from({ length: N }, () => {
    const c = document.createElement('canvas')
    c.width = S; c.height = S
    c.style.cssText = 'width:' + (S / dpr) + 'px;height:' + (S / dpr) + 'px;display:none;border-radius:7px'
    host.appendChild(c)
    return { canvas: c, ctx: c.getContext(kind) }
  })
  const atlas = document.createElement('canvas')
  atlas.width = S * 2; atlas.height = S * Math.ceil(N / 2)
  const agl = atlas.getContext('webgl2', { antialias: false, alpha: true, premultipliedAlpha: true, preserveDrawingBuffer: false })
  const au = program(agl)
  const off = new OffscreenCanvas(S, S)
  const ogl = off.getContext('webgl2', { antialias: false, alpha: true, premultipliedAlpha: true, preserveDrawingBuffer: false })
  const ou = program(ogl)
  atlas.style.cssText = 'grid-column:1 / span 2;width:' + (2 * S / dpr) + 'px;height:' + (atlas.height / dpr) + 'px;display:none'
  host.appendChild(atlas)
  const sets = { copy: cards('2d'), handoff: cards('bitmaprenderer'), one: [{ canvas: atlas }], draw: [] }
  const drawAtlas = t => { for (let i = 0; i < N; i++) { agl.viewport((i % 2) * S, Math.floor(i / 2) * S, S, S); agl.uniform1f(au.t, t); agl.uniform1f(au.k, i); agl.drawArrays(agl.TRIANGLES, 0, 6) } }
  const step = {
    copy: t => { drawAtlas(t); sets.copy.forEach(({ ctx }, i) => ctx.drawImage(atlas, (i % 2) * S, atlas.height - (Math.floor(i / 2) + 1) * S, S, S, 0, 0, S, S)) },
    handoff: t => { ogl.viewport(0, 0, S, S); sets.handoff.forEach(({ ctx }, i) => { ogl.uniform1f(ou.t, t); ogl.uniform1f(ou.k, i); ogl.drawArrays(ogl.TRIANGLES, 0, 6); ctx.transferFromImageBitmap(off.transferToImageBitmap()) }) },
    one: t => drawAtlas(t),
    draw: t => { drawAtlas(t); agl.flush() },
  }
  window.__handoff = {
    run: (mode, ms) => new Promise(resolve => {
      for (const [name, set] of Object.entries(sets)) for (const { canvas } of set) canvas.style.display = name === mode ? 'block' : 'none'
      const gaps = [], js = []
      let last = 0
      const end = performance.now() + ms
      const tick = now => {
        if (last) gaps.push(now - last)
        last = now
        const start = performance.now()
        step[mode](now / 1000)
        js.push(performance.now() - start)
        if (now < end) requestAnimationFrame(tick)
        else {
          const g = gaps.slice(5).sort((a, b) => a - b), j = js.slice(5).sort((a, b) => a - b)
          const total = g.reduce((a, b) => a + b, 0)
          resolve(JSON.stringify({ fps: +(g.length / total * 1000).toFixed(1), p50: +g[Math.floor(g.length / 2)].toFixed(1), p90: +g[Math.floor(g.length * 0.9)].toFixed(1), js: +j[Math.floor(j.length / 2)].toFixed(2) }))
        }
      }
      requestAnimationFrame(tick)
    }),
    stop: () => {
      host.remove()
      agl.getExtension('WEBGL_lose_context')?.loseContext()
      ogl.getExtension('WEBGL_lose_context')?.loseContext()
      window.__plairArt.set({ skipDraw: false })
      window.__plairScene.set({ skip: false })
      delete window.__handoff
    },
  }
  return 'ready: ' + N + ' covers of ' + S + ' px, dpr ' + dpr
})()`)
console.log(setup)
const results = Object.fromEntries(modes.map(m => [m, []]))
try {
  if (setup.startsWith('ERR')) throw new Error(setup)
  for (const mode of modes) await p.evaluate(`window.__handoff.run('${mode}', 1500)`)
  for (let r = 0; r < rounds; r++) {
    for (const mode of modes) {
      const out = JSON.parse(await p.evaluate(`window.__handoff.run('${mode}', ${seconds * 1000})`))
      results[mode].push(out)
      console.log(`round ${r + 1} ${mode.padEnd(8)} ${out.fps} fps, frame p50 ${out.p50} ms, p90 ${out.p90} ms, script ${out.js} ms`)
      await sleep(300)
    }
  }
} finally {
  await p.evaluate(`window.__handoff?.stop(), 1`)
}
const median = list => [...list].sort((a, b) => a - b)[Math.floor(list.length / 2)]
for (const [mode, list] of Object.entries(results)) {
  if (!list.length) continue
  console.log(`${mode.padEnd(8)} median ${median(list.map(x => x.fps))} fps, frame p50 ${median(list.map(x => x.p50))} ms, p90 ${median(list.map(x => x.p90))} ms, script ${median(list.map(x => x.js))} ms`)
}
p.close()
process.exit(0)
