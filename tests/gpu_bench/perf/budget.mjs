// node budget.mjs <panel> [rounds] [extra] — GPU milliseconds per frame on one panel by the slope method: the background
// scene's main pass, the lit covers as full passes and as coarse passes. Tilt pinned, lights held at a fixed level,
// each stage forced every frame with N extra copies; ms per copy = (frame with N extra - frame with none) / N.
// Covers and the background flicker while it runs; everything is put back at the end.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Playing'
const rounds = Number(process.argv[3] || 3)
const extra = Number(process.argv[4] || 6)
const p = await page()

const measureOnce = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = ms => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames (screen off or app in the background?)') })])
const settled = async () => {
  await sleep(400)
  let last = await measure(700)
  for (let i = 0; i < 6; i++) {
    const next = await measure(700)
    if (Math.abs(next - last) / next < 0.08) break
    last = next
  }
  return measure(2500)
}
const art = settings => p.evaluate(`window.__plairArt.set(${JSON.stringify(settings)}), 1`)
const scene = settings => p.evaluate(`window.__plairScene.set(${JSON.stringify(settings)}), 1`)
const STAGES = {
  'scene main pass': async n => { await art({ coarse: null, bench: { forceFull: true, extraFull: 0 } }); await scene({ force: true, extra: n }) },
  'covers, full': async n => { await scene({ force: true, extra: 0 }); await art({ coarse: false, bench: { forceFull: true, extraFull: n } }) },
  'covers, coarse': async n => { await scene({ force: true, extra: 0 }); await art({ coarse: true, bench: { forceFull: true, extraFull: n } }) },
}

await openPanel(p, panel)
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await p.evaluate('window.__plairLight.debug({ level: 0.6, kick: 0, pulse: 0 }), window.__plairArt.set({ tilt: { x: 0.6, y: 0.4 } }), 1')
await sleep(1800)

const results = Object.fromEntries(Object.keys(STAGES).map(name => [name, []]))
const bases = []
try {
  for (let round = 0; round < rounds; round++) {
    for (const [name, apply] of Object.entries(STAGES)) {
      await apply(0)
      const base = await settled()
      await apply(extra)
      const loaded = await settled()
      results[name].push((loaded - base) / extra)
      bases.push(base)
      console.log(`round ${round + 1} ${name.padEnd(16)} frame ${base} ms, +${extra} copies ${loaded} ms -> ${((loaded - base) / extra).toFixed(2)} ms each`)
    }
  }
} finally {
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  await scene({ force: false, extra: 0 })
  await p.evaluate(`(() => { window.__plairArt.set({ coarse: null, bench: { forceFull: false, extraFull: 0, extraCopy: 0 }, tilt: null }); window.__plairLight.debug({ off: false, level: null, kick: null, pulse: null }); return 1 })()`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
console.log(`${panel}: frame with everything forced once: ${median(bases).toFixed(1)} ms`)
for (const [name, values] of Object.entries(results)) console.log(`${panel}: ${name.padEnd(16)} ${median(values).toFixed(2)} ms  (runs ${values.map(v => v.toFixed(2)).join(', ')})`)
p.close()
process.exit(0)
