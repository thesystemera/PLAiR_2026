import { page, sleep } from './cdp.mjs'

// GPU ms of the background scene's main pass per variant on the phone (slope method: forced redraw + N extra main
// passes), with the covers not drawing. Variants override scene uniforms for the run.
// node scenestages.mjs '<json {label: uniforms}>' <rounds> [extra] [panel]
const variants = Object.entries(JSON.parse(process.argv[2] || '{"as is":{}}'))
const rounds = Number(process.argv[3] || 3)
const extra = Number(process.argv[4] || 4)
const panel = process.argv[5] || 'Playing'

const p = await page()
const measureOnce = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = (ms) => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames (screen off or app in the background?)') })])
await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(panel)}).click(), 1`)
await sleep(1500)
await p.evaluate(`window.__plairArt.set({ skipDraw: true, tilt: { x: 0.6, y: 0.4 } }), 1`)
const names = [...new Set(variants.flatMap(([, uniforms]) => Object.keys(uniforms)))]
const original = await p.evaluate(`window.__plairScene.eval(${JSON.stringify(`const u = { ...scene.bgMaterial.uniforms, ...scene.fgMaterial.uniforms }; return Object.fromEntries(${JSON.stringify(names)}.map(name => [name, u[name]?.value]))`)})`)
console.log('scene values before', JSON.stringify(original))
const results = Object.fromEntries(variants.map(([label]) => [label, []]))
try {
  for (let r = 0; r < rounds; r++) {
    for (const [label, uniforms] of variants) {
      const times = []
      for (const n of [0, extra]) {
        await p.evaluate(`window.__plairScene.set({ force: true, extra: ${n}, uniforms: ${JSON.stringify(uniforms)} }), 1`)
        await sleep(400)
        let last = await measure(600)
        for (let settle = 0; settle < 5; settle++) {
          const next = await measure(600)
          if (Math.abs(next - last) / next < 0.08) break
          last = next
        }
        times.push(await measure(2500))
      }
      const perPass = (times[1] - times[0]) / extra
      results[label].push(perPass)
      console.log(`round ${r + 1} ${label.padEnd(20)} frame ${times[0]} ms, +${extra} passes ${times[1]} ms -> ${perPass.toFixed(2)} ms/pass`)
    }
  }
} finally {
  await p.evaluate(`window.__plairScene.set({ force: true, extra: 0, uniforms: ${JSON.stringify(original)} }), 1`)
  await sleep(300)
  await p.evaluate(`window.__plairScene.set({ force: false, extra: 0, uniforms: null }), window.__plairArt.set({ skipDraw: false, tilt: null }), 1`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const base = median(results[variants[0][0]])
for (const [label, values] of Object.entries(results)) {
  const m = median(values)
  console.log(label.padEnd(20), 'median', m.toFixed(2), 'ms/pass', label === variants[0][0] ? '' : `(${(m - base >= 0 ? '+' : '') + (m - base).toFixed(2)})`, 'runs', values.map(v => v.toFixed(2)).join(', '))
}
p.close()
process.exit(0)
