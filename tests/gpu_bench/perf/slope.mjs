import { page, sleep } from './cdp.mjs'
const p = await page()
if (process.argv.includes('reload')) { await p.send('Page.reload', { ignoreCache: true }); await sleep(11000) }
const measure = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else { const d = []; for (let i = 1; i < f.length; i++) d.push(f[i] - f[i - 1]); d.sort((a, b) => a - b); resolve({ fps: +((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1), ms: +(d.reduce((a, b) => a + b, 0) / d.length).toFixed(2) }) } }; requestAnimationFrame(tick) })`)
await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(process.argv[2] || 'Playing')}).click(), 1`)
await sleep(1800)
const variants = JSON.parse(process.argv[3])
for (const [label, bench, extra, lightOff] of variants) {
  await p.evaluate(`window.__plairArt.set({ bench: ${JSON.stringify({ forceFull: true, extraFull: 0, extraCopy: 0, ...bench })}, ${extra || 'stepScale: 1'} }), window.__plairLight.debug({ off: ${!!lightOff} }), 1`)
  await sleep(600)
  const r = await measure(3500)
  console.log(label.padEnd(30), r.fps, 'fps', r.ms, 'ms/frame')
}
await p.evaluate(`window.__plairArt.set({ bench: { forceFull: false, extraFull: 0, extraCopy: 0 }, stepScale: 1 }), window.__plairLight.debug({ off: false }), window.__plairScene.set({ skip: false }), 1`)
p.close(); process.exit(0)
