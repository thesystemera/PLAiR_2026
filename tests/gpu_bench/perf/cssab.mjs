// node cssab.mjs <panel> '<json: {"label": "css rules", ...}>' [rounds] [extra cover passes] — GPU cost of page styling:
// the frame is loaded with forced cover and scene passes so it is GPU-bound, then each set of CSS rules is injected in
// turn (interleaved with "as is") and the settled frame time compared. The page flickers; rules are removed at the end.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Playing'
const variants = { 'as is': '', ...JSON.parse(process.argv[3]) }
const rounds = Number(process.argv[4] || 3)
const extra = Number(process.argv[5] || 1)
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
const setCss = rules => p.evaluate(`(() => { document.getElementById('bench-css')?.remove(); const rules = ${JSON.stringify(rules)}; if (rules) { const s = document.createElement('style'); s.id = 'bench-css'; s.textContent = rules; document.head.appendChild(s) } return 1 })()`)

await openPanel(p, panel)
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await p.evaluate(`window.__plairLight.debug({ level: 0.6, kick: 0, pulse: 0 }), window.__plairArt.set({ tilt: { x: 0.6, y: 0.4 }, coarse: false, bench: { forceFull: true, extraFull: ${extra} } }), window.__plairScene.set({ force: true }), 1`)
await sleep(1800)
const results = Object.fromEntries(Object.keys(variants).map(label => [label, []]))
try {
  for (let round = 0; round < rounds; round++) {
    const order = Object.entries(variants)
    for (const [label, rules] of round % 2 ? order.reverse() : order) {
      await setCss(rules)
      results[label].push(await settled())
    }
  }
} finally {
  await setCss('')
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  await p.evaluate(`(() => { window.__plairArt.set({ coarse: null, bench: { forceFull: false, extraFull: 0, extraCopy: 0 }, tilt: null }); window.__plairScene.set({ force: false }); window.__plairLight.debug({ off: false, level: null, kick: null, pulse: null }); return 1 })()`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const base = median(results['as is'])
for (const [label, values] of Object.entries(results)) {
  const m = median(values)
  console.log(`${panel}: ${label.padEnd(28)} ${m.toFixed(1)} ms ${label === 'as is' ? '       ' : `(${(m - base >= 0 ? '+' : '') + (m - base).toFixed(1)})`}  runs ${values.join(', ')}`)
}
p.close()
process.exit(0)
