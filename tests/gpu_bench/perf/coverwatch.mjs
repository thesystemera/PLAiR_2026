// node coverwatch.mjs [distance px] [speed px/s] [rounds] — scrolls the Catalog down and back with a synthetic finger
// and, on every frame, looks at each cover on screen: not shown yet (placeholder), still fading in, or fully there.
// Prints the share of cover-frames in each state for the way down and the way back, which is what "the covers were
// gone when I scrolled back" measures, and how many cards were absent altogether (fewer cards on screen than when the
// list was at rest: the list lost its place). Reading the styles every frame slows the scroll a little; compare builds, not
// absolute frame rates. DIRECT=1 moves the list by its scroll position instead of a finger (for desktop Chrome).
// IDLEMB=<n> sets how many megabytes of covers the renderer keeps on the GPU after they leave the screen for this run
// (the page keeps it until a reload). The last line is the renderer's texture count and memory.
import { page, sleep, openPanel } from './cdp.mjs'

const distance = Number(process.argv[2] || 1800)
const speed = Number(process.argv[3] || 2500)
const rounds = Number(process.argv[4] || 3)
const p = await page()
await openPanel(p, 'Catalog')
await sleep(2000)
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
if (process.env.IDLEMB) await p.evaluate(`window.__plairArt.set({ idleMB: ${Number(process.env.IDLEMB)} }), 1`)
const watch = () => p.evaluate(`(() => {
  const counts = { missing: 0, fading: 0, shown: 0, frames: 0, absent: 0 }
  let running = true
  let atRest = -1
  const panel = document.querySelector('[data-shader-panel="catalog"]')
  const tick = () => {
    if (!running) return
    counts.frames++
    let onScreen = 0
    for (const canvas of panel.querySelectorAll('canvas[role=img]')) {
      const box = canvas.getBoundingClientRect()
      if (box.width < 60 || box.bottom < 120 || box.top > innerHeight - 160) continue
      onScreen++
      const opacity = parseFloat(getComputedStyle(canvas).opacity)
      if (opacity < 0.05) counts.missing++
      else if (opacity < 0.95) counts.fading++
      else counts.shown++
    }
    if (atRest < 0) atRest = onScreen
    counts.absent += Math.max(0, atRest - onScreen)
    requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
  window.__coverWatch = { counts, stop: () => { running = false } }
  return 1
})()`)
const read = () => p.evaluate(`(() => { window.__coverWatch.stop(); return JSON.stringify(window.__coverWatch.counts) })()`).then(JSON.parse)
const blank = () => ({ missing: 0, fading: 0, shown: 0, frames: 0, absent: 0 })
const totals = { down: blank(), back: blank() }
for (let round = 0; round < rounds; round++) {
  for (const [leg, direction] of [['down', -1], ['back', 1]]) {
    await watch()
    if (process.env.DIRECT) {
      await p.evaluate(`(async () => {
        const el = [...document.querySelectorAll('[data-scroller]')].find(e => e.closest('[data-shader-panel="catalog"]') && e.scrollHeight > e.clientHeight + 10)
        const steps = Math.max(1, Math.round(${distance} / (${speed} / 60)))
        for (let i = 0; i < steps; i++) { el.scrollTop += ${-direction * distance} / steps; await new Promise(r => requestAnimationFrame(r)) }
        return 1
      })()`)
    } else {
      await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
    }
    await sleep(300)
    const counts = await read()
    for (const key of Object.keys(counts)) totals[leg][key] += counts[key]
    await sleep(700)
  }
}
for (const [leg, c] of Object.entries(totals)) {
  const all = c.missing + c.fading + c.shown + c.absent || 1
  console.log(`${leg === 'down' ? 'way down' : 'way back'}: no card there ${(c.absent / all * 100).toFixed(1)}%, cover not shown ${(c.missing / all * 100).toFixed(1)}%, still fading in ${(c.fading / all * 100).toFixed(1)}%, fully there ${(c.shown / all * 100).toFixed(1)}%  (${c.frames} frames, ${all} cover-frames)`)
}
console.log('renderer: ' + await p.evaluate(`JSON.stringify(window.__plairArt?.stats?.() || {})`))
p.close()
process.exit(0)
