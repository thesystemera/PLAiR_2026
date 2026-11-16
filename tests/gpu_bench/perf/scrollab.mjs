// node scrollab.mjs [panel] [rounds] [distance px] [speed px/s] ['<json {label: {art, scene, css, js, undo}}>'] — what
// each part costs while a panel is scrolled with a synthetic finger: frames per second, frames over 25 ms and frame
// time lost per second of scrolling, with switches applied in turn, interleaved, medians. A variant can set cover
// switches (`art`, window.__plairArt.set), scene switches (`scene`, window.__plairScene.set), inject CSS (`css`) and
// run page code (`js`, put back with `undo`). Default variants: the arrival fades off, covers flat, no scene.
// Covers and the background flicker while it runs; everything is put back at the end.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Catalog'
const rounds = Number(process.argv[3] || 3)
const distance = Number(process.argv[4] || 1800)
const speed = Number(process.argv[5] || 2500)
const NO_FADES = '.ui-arrive { transition: none !important } .ui-placeholder { animation: none !important }'
const variants = Object.entries(process.argv[6] ? JSON.parse(process.argv[6]) : {
  'as is': {},
  'no arrival fades': { css: NO_FADES },
  'covers flat': { art: { lit: false } },
  'no scene': { scene: { skip: true } },
  'flat + no scene': { art: { lit: false }, scene: { skip: true } },
})
const RESET_ART = { coarse: null, lit: true }
const RESET_SCENE = { skip: false }

const p = await page()
await openPanel(p, panel)
await sleep(1800)
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
const apply = variant => p.evaluate(`(() => {
  window.__plairArt?.set(${JSON.stringify({ ...RESET_ART, ...variant.art })})
  window.__plairScene?.set(${JSON.stringify({ ...RESET_SCENE, ...variant.scene })})
  document.getElementById('scrollab-css')?.remove()
  const css = ${JSON.stringify(variant.css || '')}
  if (css) { const style = document.createElement('style'); style.id = 'scrollab-css'; style.textContent = css; document.head.appendChild(style) }
  ${variant.js || ''}
  return 1
})()`)
const undo = variant => (variant.undo ? p.evaluate(`(() => { ${variant.undo}; return 1 })()`) : null)
const scroll = async () => {
  await p.evaluate(`(() => {
    const frames = []
    let last = performance.now()
    let running = true
    const tick = t => { frames.push(t - last); last = t; if (running) requestAnimationFrame(tick) }
    requestAnimationFrame(tick)
    window.__scrollAb = { frames, stop: () => { running = false } }
    return 1
  })()`)
  for (const direction of [-1, 1]) {
    await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
    await sleep(200)
  }
  return JSON.parse(await p.evaluate(`(() => {
    const w = window.__scrollAb
    w.stop()
    const gaps = w.frames.slice(3)
    const total = gaps.reduce((a, b) => a + b, 0)
    return JSON.stringify({ fps: gaps.length / total * 1000, over25: gaps.filter(g => g > 25).length / total * 1000, lost: gaps.filter(g => g > 25).reduce((a, g) => a + g - 16.7, 0) / total * 1000, worst: Math.max(...gaps) })
  })()`))
}

const results = new Map(variants.map(([label]) => [label, []]))
try {
  for (let round = 0; round < rounds; round++) {
    for (const [label, variant] of round % 2 ? [...variants].reverse() : variants) {
      await apply(variant)
      await sleep(900)
      results.get(label).push(await scroll())
      await undo(variant)
      await sleep(500)
    }
  }
} finally {
  await apply({})
}
const median = (runs, key) => [...runs.map(run => run[key])].sort((a, b) => a - b)[Math.floor(runs.length / 2)]
console.log(`${panel}, ${rounds} rounds of a ${distance} px scroll down and back at ${speed} px/s (medians)`)
for (const [label, runs] of results) {
  console.log(`${label.padEnd(26)} ${median(runs, 'fps').toFixed(1).padStart(5)} fps, ${median(runs, 'over25').toFixed(1).padStart(4)} slow frames/s, ${median(runs, 'lost').toFixed(0).padStart(4)} ms lost/s, worst ${median(runs, 'worst').toFixed(0).padStart(4)} ms   [fps per round: ${runs.map(run => run.fps.toFixed(0)).join(', ')}]`)
}
p.close()
process.exit(0)
