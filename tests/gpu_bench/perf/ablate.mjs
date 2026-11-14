// node ablate.mjs <panel> [rounds] [rest] — where the frame time goes on one panel: frames per second under simulated
// hand-held tilt (or at rest with `rest`) with the covers and the background scene switched off in turn, interleaved,
// medians. Covers and the background flicker while it runs; everything is put back at the end.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Catalog'
const rounds = Number(process.argv[3] || 3)
const still = process.argv.includes('rest')
const VARIANTS = [
  ['as is', { coarse: null, lit: true }, { skip: false }],
  ['coarse covers', { coarse: true, lit: true }, { skip: false }],
  ['covers flat', { coarse: null, lit: false }, { skip: false }],
  ['no scene', { coarse: null, lit: true }, { skip: true }],
  ['flat + no scene', { coarse: null, lit: false }, { skip: true }],
]

const p = await page()
let wobbling = !still
const wobble = (async () => {
  const start = Date.now()
  while (wobbling) {
    const t = (Date.now() - start) / 1000
    await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 10 * Math.sin(t * Math.PI * 1.6), gamma: 10 * Math.sin(t * Math.PI * 1.2 + 1) })
    await sleep(33)
  }
})()
const measure = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1)) }; requestAnimationFrame(tick) })`)
const apply = (art, scene) => p.evaluate(`window.__plairArt.set(${JSON.stringify(art)}), window.__plairScene.set(${JSON.stringify(scene)}), 1`)

await openPanel(p, panel)
await sleep(1800)
const results = Object.fromEntries(VARIANTS.map(([label]) => [label, []]))
try {
  for (let round = 0; round < rounds; round++) {
    for (const [label, art, scene] of round % 2 ? [...VARIANTS].reverse() : VARIANTS) {
      await apply(art, scene)
      await sleep(900)
      results[label].push(await measure(3000))
    }
  }
} finally {
  wobbling = false
  await wobble
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  await apply({ coarse: null, lit: true }, { skip: false })
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
for (const [label, values] of Object.entries(results)) {
  const fps = median(values)
  console.log(panel.padEnd(8), (still ? 'rest' : 'tilt'), label.padEnd(16), `${fps} fps`.padEnd(9), `${(1000 / fps).toFixed(1)} ms`.padEnd(8), 'runs', values.join(', '))
}
p.close()
process.exit(0)
