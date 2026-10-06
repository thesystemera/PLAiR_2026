import { page, sleep } from './cdp.mjs'

// fps with the phone at rest (real sensors, music driving the lights), coarse off vs on, alternating.
const panels = (process.argv[2] || 'Playing,Catalog').split(',')
const rounds = Number(process.argv[3] || 3)
const p = await page()
const measureOnce = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1)) }; requestAnimationFrame(tick) })`)
const measure = (ms) => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])
try {
  for (const panel of panels) {
    await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(panel)}).click(), 1`)
    await sleep(1500)
    const fps = { off: [], on: [] }
    for (let r = 0; r < rounds; r++) {
      for (const mode of r % 2 ? ['on', 'off'] : ['off', 'on']) {
        await p.evaluate(`window.__plairArt.set({ coarse: ${mode === 'on'} }), 1`)
        await sleep(800)
        fps[mode].push(await measure(3000))
      }
    }
    const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
    console.log(`${panel} at rest: coarse off ${median(fps.off)} fps (${fps.off.join(', ')}) | coarse on ${median(fps.on)} fps (${fps.on.join(', ')})`)
  }
} finally {
  await p.evaluate(`window.__plairArt.set({ coarse: true }), 1`)
}
p.close()
process.exit(0)
