// node flingstages.mjs '<stage,stage>' [rounds] [distance] [speed] - Catalog fling page rate (requestAnimationFrame
// updates a second while a synthetic finger scrolls down and back) with cover-shader stages from edits.mjs applied in
// turn (live shader edits and derived per-cover uniforms, coarse covers off), interleaved, medians. Nothing is deployed;
// the shaders are put back at the end.
import { page, sleep, openPanel } from './cdp.mjs'
import { STAGES, installShaderEdits, applyStage, removeShaderEdits } from './edits.mjs'

const names = (process.argv[2] || 'as is,fill loop + per cover').split(',')
const rounds = Number(process.argv[3] || 3)
const distance = Number(process.argv[4] || 1800)
const speed = Number(process.argv[5] || 3000)

const p = await page()
await installShaderEdits(p)
await openPanel(p, 'Catalog')
await p.evaluate(`[...document.querySelectorAll('[data-scroller]')].forEach(s => s.scrollTop = 0), 1`)
await sleep(1500)
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
const fling = async () => {
  await p.evaluate(`(() => {
    const frames = []
    let last = performance.now()
    let running = true
    const tick = t => { frames.push(t - last); last = t; if (running) requestAnimationFrame(tick) }
    requestAnimationFrame(tick)
    window.__fling = { frames, stop: () => { running = false } }
    return 1
  })()`)
  for (const direction of [-1, 1]) {
    await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
    await sleep(200)
  }
  return Number(await p.evaluate(`(() => { const w = window.__fling; w.stop(); const gaps = w.frames.slice(3); return gaps.length / gaps.reduce((a, b) => a + b, 0) * 1000 })()`))
}
const results = Object.fromEntries(names.map(name => [name, []]))
try {
  for (let round = 0; round < rounds; round++) {
    for (const name of round % 2 ? [...names].reverse() : names) {
      await applyStage(p, STAGES[name])
      await sleep(1500)
      const rate = await fling()
      results[name].push(rate)
      console.log(`round ${round + 1} ${name.padEnd(24)} ${rate.toFixed(1)} page updates/s`)
    }
  }
} finally {
  await removeShaderEdits(p)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
for (const [name, values] of Object.entries(results)) console.log(`${name.padEnd(24)} median ${median(values).toFixed(1)} page updates/s  [${values.map(v => v.toFixed(1)).join(', ')}]`)
p.close()
process.exit(0)
