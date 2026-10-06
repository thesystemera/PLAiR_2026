import { page, sleep } from './cdp.mjs'

// Moving-frame fps with coarse parallax and lighting off vs on: scene and covers forced to redraw every frame,
// tilt pinned, light level fixed, alternating. node coarsefps.mjs <panels> <rounds>
const panels = (process.argv[2] || 'Playing,Catalog').split(',')
const rounds = Number(process.argv[3] || 3)
const p = await page()
const measureOnce = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1)) }; requestAnimationFrame(tick) })`)
const measure = (ms) => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])
try {
  await p.evaluate(`window.__plairScene.set({ force: true, extra: 0 }), window.__plairLight.debug({ level: 0.6, kick: 0, pulse: 0 }), 1`)
  for (const panel of panels) {
    await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(panel)}).click(), 1`)
    await sleep(1500)
    const fps = { off: [], on: [] }
    for (let r = 0; r < rounds; r++) {
      for (const mode of r % 2 ? ['on', 'off'] : ['off', 'on']) {
        await p.evaluate(`window.__plairArt.set({ coarse: ${mode === 'on'}, bench: { forceFull: true, extraFull: 0 }, tilt: { x: 0.6, y: 0.4 } }), 1`)
        await sleep(600)
        await measure(800)
        fps[mode].push(await measure(3000))
      }
    }
    const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
    console.log(`${panel}: today ${median(fps.off)} fps (${fps.off.join(', ')}) | coarse ${median(fps.on)} fps (${fps.on.join(', ')})`)
  }
} finally {
  await p.evaluate(`window.__plairScene.set({ force: false, extra: 0 }), window.__plairArt.set({ coarse: false, bench: { forceFull: false, extraFull: 0 }, tilt: null }), window.__plairLight.debug({ level: null, kick: null, pulse: null }), 1`)
}
p.close()
process.exit(0)
