// node scenecopy.mjs [rounds] — what it costs on the phone to copy pixels out of the live background canvas right
// after it has drawn, the way the scroller's edge-fade strips do. Variants, all with the fade forced to the CSS mask
// so only the test copies run: none; 1 or 4 small copies straight from the scene; one whole-scene copy into a
// holding canvas and 4 small copies from that. The frame is loaded with forced cover and scene passes. Cleans up.
import { page, sleep, openPanel } from './cdp.mjs'

const rounds = Number(process.argv[2] || 3)
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

await openPanel(p, 'Playing')
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await p.evaluate(`(() => {
  window.__plairEdgeFade?.force('mask')
  window.__plairLight.debug({ level: 0.6, kick: 0, pulse: 0 })
  window.__plairArt.set({ tilt: { x: 0.6, y: 0.4 }, coarse: false, bench: { forceFull: true, extraFull: 1 } })
  window.__plairScene.set({ force: true })
  const scene = [...document.querySelectorAll('canvas')].find(c => !c.getAttribute('role') && !c.getAttribute('aria-hidden') && c.width > 300)
  const small = Array.from({ length: 4 }, (_, i) => { const c = document.createElement('canvas'); c.width = i < 2 ? scene.width : 12; c.height = i < 2 ? 24 : scene.height; return c.getContext('2d') })
  const holding = document.createElement('canvas')
  holding.width = scene.width
  holding.height = scene.height
  const hold = holding.getContext('2d', { alpha: false })
  const bench = window.__benchSceneCopy = { mode: 'none', raf: 0 }
  const rect = i => (i === 0 ? [0, 0] : i === 1 ? [0, scene.height - 24] : i === 2 ? [0, 0] : [scene.width - 12, 0])
  const tick = () => {
    bench.raf = requestAnimationFrame(tick)
    if (bench.mode === 'direct1' || bench.mode === 'direct4') {
      const count = bench.mode === 'direct1' ? 1 : 4
      for (let i = 0; i < count; i++) { const ctx = small[i]; const [x, y] = rect(i); ctx.drawImage(scene, x, y, ctx.canvas.width, ctx.canvas.height, 0, 0, ctx.canvas.width, ctx.canvas.height) }
    } else if (bench.mode === 'held4') {
      hold.drawImage(scene, 0, 0)
      for (let i = 0; i < 4; i++) { const ctx = small[i]; const [x, y] = rect(i); ctx.drawImage(holding, x, y, ctx.canvas.width, ctx.canvas.height, 0, 0, ctx.canvas.width, ctx.canvas.height) }
    } else if (bench.mode === 'held0') {
      hold.drawImage(scene, 0, 0)
    }
  }
  bench.raf = requestAnimationFrame(tick)
  return 1
})()`)
await sleep(1500)
const modes = { none: 'no copies', direct1: '1 small copy from the scene', direct4: '4 small copies from the scene', held0: 'one whole-scene copy', held4: 'one whole-scene copy + 4 small' }
const results = Object.fromEntries(Object.keys(modes).map(mode => [mode, []]))
try {
  for (let round = 0; round < rounds; round++) {
    const order = Object.keys(modes)
    for (const mode of round % 2 ? order.reverse() : order) {
      await p.evaluate(`window.__benchSceneCopy.mode = ${JSON.stringify(mode)}, 1`)
      results[mode].push(await settled())
    }
  }
} finally {
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  await p.evaluate(`(() => { cancelAnimationFrame(window.__benchSceneCopy.raf); delete window.__benchSceneCopy; window.__plairEdgeFade?.force(null); window.__plairArt.set({ coarse: null, bench: { forceFull: false, extraFull: 0, extraCopy: 0 }, tilt: null }); window.__plairScene.set({ force: false }); window.__plairLight.debug({ off: false, level: null, kick: null, pulse: null }); return 1 })()`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const base = median(results.none)
for (const [mode, label] of Object.entries(modes)) console.log(`${label.padEnd(32)} ${median(results[mode]).toFixed(1)} ms (${(median(results[mode]) - base >= 0 ? '+' : '') + (median(results[mode]) - base).toFixed(1)})  runs ${results[mode].join(', ')}`)
p.close()
process.exit(0)
