import { page, sleep } from './cdp.mjs'

// Where a moving frame's time goes on the phone: frame time with the background scene and the covers each forced to
// redraw every frame or switched off (tilt pinned, light level fixed). node framesplit.mjs <panels> <rounds>
const panels = (process.argv[2] || 'Playing,Radio').split(',')
const rounds = Number(process.argv[3] || 3)
const COMBOS = [
  ['scene + covers', { force: true, skip: false }, { skipDraw: false, forceFull: true }],
  ['scene only', { force: true, skip: false }, { skipDraw: true, forceFull: false }],
  ['covers only', { force: false, skip: true }, { skipDraw: false, forceFull: true }],
  ['neither', { force: false, skip: true }, { skipDraw: true, forceFull: false }],
]

const p = await page()
const measureOnce = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = (ms) => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])
const set = (scene, art) => p.evaluate(`window.__plairScene.set({ ...${JSON.stringify(scene)}, extra: 0 }), window.__plairArt.set({ skipDraw: ${art.skipDraw}, bench: { forceFull: ${art.forceFull}, extraFull: 0 }, tilt: { x: 0.6, y: 0.4 } }), window.__plairLight.debug({ level: 0.6, kick: 0, pulse: 0 }), 1`)
try {
  for (const panel of panels) {
    await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(panel)}).click(), 1`)
    await sleep(1500)
    const results = Object.fromEntries(COMBOS.map(([label]) => [label, []]))
    for (let r = 0; r < rounds; r++) {
      for (const [label, scene, art] of COMBOS) {
        await set(scene, art)
        await sleep(500)
        await measure(800)
        results[label].push(await measure(2500))
      }
    }
    const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
    console.log(panel, Object.entries(results).map(([label, values]) => `${label} ${median(values).toFixed(1)} ms (${values.join(', ')})`).join(' | '))
  }
} finally {
  await p.evaluate(`window.__plairScene.set({ force: false, skip: false, extra: 0 }), window.__plairArt.set({ skipDraw: false, bench: { forceFull: false, extraFull: 0 }, tilt: null }), window.__plairLight.debug({ level: null, kick: null, pulse: null }), 1`)
}
p.close()
process.exit(0)
