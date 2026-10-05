import { page, sleep } from './cdp.mjs'
const p = await page()
if (process.argv.includes('reload')) { await p.send('Page.reload', { ignoreCache: true }); await sleep(11000) }
const panel = process.argv[2] || 'Playing'
const variants = JSON.parse(process.argv[3])
const rounds = Number(process.argv[4] || 3)
let wobbling = true
const wobble = (async () => { const t0 = Date.now(); while (wobbling) { const t = (Date.now() - t0) / 1000; await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 10 * Math.sin(t * Math.PI * 1.6), gamma: 10 * Math.sin(t * Math.PI * 1.2 + 1) }); await sleep(33) } })()
const measure = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1)) }; requestAnimationFrame(tick) })`)
await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(panel)}).click(), 1`)
await sleep(1500)
const results = Object.fromEntries(variants.map(([label]) => [label, []]))
for (let r = 0; r < rounds; r++) {
  for (const [label, set] of variants) {
    const { lightOff, ...art } = set
    await p.evaluate(`window.__plairArt.set(${JSON.stringify(art)}), window.__plairLight.debug({ off: ${!!lightOff} }), 1`)
    await sleep(700)
    results[label].push(await measure(3000))
  }
}
wobbling = false; await wobble
await p.send('DeviceOrientation.clearDeviceOrientationOverride')
await p.evaluate(`window.__plairArt.set({ bench: { forceFull: false }, stepScale: 1, skipCopy: false, stepPx: 0 }), window.__plairLight.debug({ off: false }), 1`)
for (const [label, values] of Object.entries(results)) { const sorted = [...values].sort((a, b) => a - b); console.log(panel, label.padEnd(18), 'median', sorted[Math.floor(sorted.length / 2)], 'runs', values.join(', ')) }
p.close(); process.exit(0)
