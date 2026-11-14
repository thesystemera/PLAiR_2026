// node frame.mjs <panel> '<json list of [label, art settings, scene settings, page code?]>' [rounds] — settled average frame
// time for each configuration, interleaved, tilt pinned and lights held. For working out fixed costs: load a stage
// with extra copies so the frame is GPU-bound, then subtract the known cost of the copies (budget.mjs).
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Playing'
const configs = JSON.parse(process.argv[3])
const rounds = Number(process.argv[4] || 2)
const p = await page()

const measureOnce = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = ms => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])
const settled = async () => {
  await sleep(500)
  let last = await measure(700)
  for (let i = 0; i < 6; i++) {
    const next = await measure(700)
    if (Math.abs(next - last) / next < 0.08) break
    last = next
  }
  return measure(2500)
}
const RESET_ART = { coarse: null, lit: true, skipCopy: false, bench: { forceFull: false, extraFull: 0, extraCopy: 0 } }
const RESET_SCENE = { skip: false, force: false, extra: 0, uniforms: null }

await openPanel(p, panel)
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await p.evaluate('window.__plairLight.debug({ level: 0.6, kick: 0, pulse: 0 }), window.__plairArt.set({ tilt: { x: 0.6, y: 0.4 } }), 1')
await sleep(1800)
const results = Object.fromEntries(configs.map(([label]) => [label, []]))
try {
  for (let round = 0; round < rounds; round++) {
    for (const [label, art, scene, code] of configs) {
      await p.evaluate(`window.__plairArt.set(${JSON.stringify({ ...RESET_ART, ...art, bench: { ...RESET_ART.bench, ...(art?.bench || {}) } })}), window.__plairScene.set(${JSON.stringify({ ...RESET_SCENE, ...scene })}), 1`)
      if (code) await p.evaluate(code)
      results[label].push(await settled())
    }
  }
} finally {
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  await p.evaluate(`(() => { window.__plairArt.set(${JSON.stringify({ ...RESET_ART, tilt: null })}); window.__plairScene.set(${JSON.stringify(RESET_SCENE)}); window.__plairLight.debug({ off: false, level: null, kick: null, pulse: null }); return 1 })()`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
for (const [label, values] of Object.entries(results)) console.log(`${panel}: ${label.padEnd(34)} ${median(values).toFixed(1)} ms  (runs ${values.join(', ')})`)
p.close()
process.exit(0)
