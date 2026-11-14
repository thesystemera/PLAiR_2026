// node copycost.mjs [size] [rounds] [targets] — what WebGL-to-2D-canvas copies cost on the phone, which is how every
// cover reaches the screen. `targets` test canvases of size x size device pixels sit (almost invisible) along the top
// and are all updated every frame three ways: a plain 2D fill ("fill"), a copy from one throwaway WebGL canvas that
// redraws a flat colour each frame ("copy"), and the same copy done three times with a 1-pixel inset so none can be
// skipped ("copy x3"). The frame is loaded with extra scene passes so it is GPU-bound; covers are flat. Cleans up.
import { page, sleep } from './cdp.mjs'

const size = Number(process.argv[2] || 989)
const rounds = Number(process.argv[3] || 3)
const targets = Number(process.argv[4] || 1)
const p = await page()

const measureOnce = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const settled = async () => {
  await sleep(500)
  let last = await measureOnce(700)
  for (let i = 0; i < 6; i++) {
    const next = await measureOnce(700)
    if (Math.abs(next - last) / next < 0.08) break
    last = next
  }
  return measureOnce(2500)
}

await p.evaluate(`(() => {
  const size = ${size}
  const count = ${targets}
  const css = size / devicePixelRatio
  const contexts = []
  for (let i = 0; i < count; i++) {
    const target = document.createElement('canvas')
    target.className = 'bench-copy-target'
    target.width = target.height = size
    target.style.cssText = 'position:fixed;left:' + (i % 8) * (css + 2) + 'px;top:' + Math.floor(i / 8) * (css + 2) + 'px;width:' + css + 'px;height:' + css + 'px;opacity:0.02;pointer-events:none;z-index:99999'
    document.body.appendChild(target)
    contexts.push(target.getContext('2d', { alpha: false }))
  }
  const source = document.createElement('canvas')
  source.width = source.height = size
  const gl = source.getContext('webgl2', { alpha: false, antialias: false })
  const bench = window.__benchCopy = { mode: 'none', frame: 0, raf: 0, gl }
  const tick = () => {
    bench.raf = requestAnimationFrame(tick)
    const shade = (bench.frame++ % 64) / 64
    if (bench.mode === 'fill') {
      for (const ctx of contexts) {
        ctx.fillStyle = 'rgb(' + Math.round(shade * 255) + ',40,90)'
        ctx.fillRect(0, 0, size, size)
      }
    } else if (bench.mode === 'copy' || bench.mode === 'copy3') {
      gl.clearColor(shade, 0.2, 0.4, 1)
      gl.clear(gl.COLOR_BUFFER_BIT)
      for (const ctx of contexts) {
        ctx.drawImage(source, 0, 0)
        if (bench.mode === 'copy3') {
          ctx.drawImage(source, 1, 1, size - 2, size - 2, 1, 1, size - 2, size - 2)
          ctx.drawImage(source, 2, 2, size - 4, size - 4, 2, 2, size - 4, size - 4)
        }
      }
    }
  }
  bench.raf = requestAnimationFrame(tick)
  return 1
})()`)
await p.evaluate(`window.__plairArt.set({ lit: false }), window.__plairScene.set({ force: true, extra: 3 }), 1`)
await sleep(1500)
const modes = ['none', 'fill', 'copy', 'copy3']
const results = Object.fromEntries(modes.map(mode => [mode, []]))
try {
  for (let round = 0; round < rounds; round++) {
    for (const mode of round % 2 ? [...modes].reverse() : modes) {
      await p.evaluate(`window.__benchCopy.mode = ${JSON.stringify(mode)}, 1`)
      results[mode].push(await settled())
    }
  }
} finally {
  await p.evaluate(`(() => { const b = window.__benchCopy; cancelAnimationFrame(b.raf); b.gl.getExtension('WEBGL_lose_context')?.loseContext(); document.querySelectorAll('.bench-copy-target').forEach(el => el.remove()); delete window.__benchCopy; window.__plairArt.set({ lit: true }); window.__plairScene.set({ force: false, extra: 0 }); return 1 })()`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const base = median(results.none)
const labels = { none: 'canvases not updated', fill: 'each updated by a 2D fill', copy: 'each updated by a WebGL copy', copy3: 'three WebGL copies each' }
for (const mode of modes) console.log(`${targets} x ${size}x${size}: ${labels[mode].padEnd(28)} ${median(results[mode]).toFixed(1)} ms (${(median(results[mode]) - base >= 0 ? '+' : '') + (median(results[mode]) - base).toFixed(1)})  runs ${results[mode].join(', ')}`)
p.close()
process.exit(0)
