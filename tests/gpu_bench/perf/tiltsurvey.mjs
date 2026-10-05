import { page, sleep } from './cdp.mjs'
const p = await page()
const panels = (process.argv[2] || 'Catalog,Queue,Radio,Playing').split(',')
const seconds = Number(process.argv[3] || 5)
let wobbling = true
const base = { beta: 0, gamma: 0 }
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, ...base })
const wobble = (async () => {
  const t0 = Date.now()
  while (wobbling) {
    const t = (Date.now() - t0) / 1000
    await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: base.beta + 10 * Math.sin(t * Math.PI * 1.6), gamma: 10 * Math.sin(t * Math.PI * 1.2 + 1) })
    await sleep(33)
  }
})()
const measure = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else { const d = []; for (let i = 1; i < f.length; i++) d.push(f[i] - f[i - 1]); d.sort((a, b) => a - b); resolve(JSON.stringify({ fps: +((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1), p50: +d[Math.floor(d.length / 2)].toFixed(1), p90: +d[Math.floor(d.length * 0.9)].toFixed(1) })) } }; requestAnimationFrame(tick) })`)
await sleep(1500)
console.log('gyro', await p.evaluate(`window.__plairScene.eval("const g = scene.state?.gyro; return g ? [+g.parallaxX.toFixed(2), +g.parallaxY.toFixed(2)] : null")`))
for (const panel of panels) {
  await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(panel)}).click(), 1`)
  await sleep(1800)
  await p.evaluate(`window.__plairProfile = {}, 1`)
  const all = await measure(seconds * 1000)
  const modes = await p.evaluate(`(() => { const t = window.__plairProfile?.tiles || {}; window.__plairProfile = null; return 'full ' + (t.full || 0) + ' build ' + (t.build || 0) + ' relight ' + (t.relight || 0) + ' frames ' + (t.frames || 0) })()`)
  console.log(panel.padEnd(8), all, modes)
}
wobbling = false
await wobble
await p.send('DeviceOrientation.clearDeviceOrientationOverride')
p.close(); process.exit(0)
