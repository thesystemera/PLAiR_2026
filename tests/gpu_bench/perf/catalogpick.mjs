// node catalogpick.mjs [picks] [warm-up] — taps Catalog cards on the phone one after another (5 s apart) and prints, per
// pick and on average: ms from the tap to the first change inside the card (its first visible answer), to the card
// being marked as the playing one and to the player showing the new song; frame time lost in the 1.5 s after the tap,
// worst frame, frames over 50 ms, and the long frames split into script, end-of-frame style and layout and the rest.
// SCROLL=<px> first scrolls the Catalog down and back by that distance (finger speed SPEED px/s, default 2500) and
// prints the frame rate, the worst frame and the frames over 25 ms while it moves. VARIANTS='{"label": "css"}' runs
// a series per set of injected CSS rules (or {css, art, scene}: cover and scene switches as in scrollab.mjs), in turn,
// ROUNDS times (default 2). It changes the owner's song on every
// pick.
import { page, sleep, openPanel } from './cdp.mjs'

const picks = Number(process.argv[2] || 8)
const warm = Number(process.argv[3] || 1)
const WINDOW_MS = 1500
const p = await page()
await openPanel(p, 'Catalog')
await sleep(2000)
await p.evaluate(`(() => {
  if (window.__pickSeries) return 1
  const watch = window.__pickSeries = { long: [], next: 0 }
  new PerformanceObserver(list => {
    for (const e of list.getEntries()) {
      watch.long.push({ at: e.startTime, ms: e.duration, style: e.styleAndLayoutStart ? e.startTime + e.duration - e.styleAndLayoutStart : 0, script: e.scripts.reduce((sum, s) => sum + s.duration, 0) })
    }
  }).observe({ type: 'long-animation-frame' })
  return 1
})()`)

const frameWatch = `
  const frames = []
  let last = performance.now()
  let running = true
  const tick = t => { frames.push([t, t - last]); last = t; if (running) requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
`

if (process.env.SCROLL) {
  const distance = Number(process.env.SCROLL)
  const speed = Number(process.env.SPEED || 2500)
  const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
  for (const direction of [-1, 1, -1, 1]) {
    await p.evaluate(`(() => { ${frameWatch}; window.__scrollWatch = { frames, stop: () => { running = false } }; return 1 })()`)
    await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
    const out = JSON.parse(await p.evaluate(`(() => {
      const w = window.__scrollWatch
      w.stop()
      const gaps = w.frames.slice(2).map(f => f[1])
      const total = gaps.reduce((a, b) => a + b, 0)
      return JSON.stringify({ fps: +(gaps.length / total * 1000).toFixed(1), worst: Math.round(Math.max(...gaps)), over25: gaps.filter(g => g > 25).length, lost: Math.round(gaps.filter(g => g > 25).reduce((a, g) => a + g - 16.7, 0)), seconds: +(total / 1000).toFixed(1) })
    })()`))
    console.log(`scroll ${direction < 0 ? 'down' : 'up  '}: ${out.fps} fps over ${out.seconds} s, worst frame ${out.worst} ms, ${out.over25} frames over 25 ms (${out.lost} ms lost)`)
    await sleep(1200)
  }
  await sleep(1500)
}

const pick = () => p.evaluate(`(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  const visible = [...document.querySelectorAll('[class*="group/card"]')].filter(card => {
    const r = card.getBoundingClientRect()
    return r.width > 40 && r.top > 110 && r.bottom < innerHeight - 200 && r.left >= 0 && r.right <= innerWidth
  })
  if (visible.length < 2) return JSON.stringify({ error: 'fewer than two Catalog cards on screen' })
  const watch = window.__pickSeries
  const card = visible[watch.next++ % visible.length]
  const player = document.querySelector('[data-shader-panel="player"]')
  ${frameWatch}
  await sleep(600)
  const classBefore = card.className
  const playerBefore = player?.innerText
  let first = 0, marked = 0, shown = 0
  const stamp = () => performance.now()
  const cardObserver = new MutationObserver(() => {
    if (!first) first = stamp()
    if (!marked && card.className !== classBefore) marked = stamp()
  })
  cardObserver.observe(card, { attributes: true, childList: true, subtree: true })
  const playerObserver = new MutationObserver(() => { if (!shown && player.innerText !== playerBefore) shown = stamp() })
  if (player) playerObserver.observe(player, { childList: true, subtree: true, characterData: true })
  const r = card.getBoundingClientRect()
  const at = { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 3, pointerId: 1, pointerType: 'touch', isPrimary: true }
  watch.long.length = 0
  const pressed = performance.now()
  card.dispatchEvent(new PointerEvent('pointerdown', at))
  card.dispatchEvent(new PointerEvent('pointerup', at))
  await sleep(${WINDOW_MS + 300})
  running = false
  cardObserver.disconnect()
  playerObserver.disconnect()
  const after = frames.filter(f => f[0] >= pressed && f[0] <= pressed + ${WINDOW_MS})
  const painted = at => { if (!at) return null; const frame = frames.find(f => f[0] >= at); return Math.round((frame ? frame[0] : at) - pressed) }
  let script = 0, style = 0, longMs = 0
  for (const frame of watch.long) {
    if (frame.at + frame.ms < pressed || frame.at > pressed + ${WINDOW_MS}) continue
    script += frame.script
    style += frame.style
    longMs += frame.ms
  }
  return JSON.stringify({
    firstMs: painted(first), markedMs: painted(marked), playerMs: painted(shown),
    lostMs: Math.round(after.filter(f => f[1] > 25).reduce((a, f) => a + f[1] - 16.7, 0)),
    worstMs: Math.round(Math.max(...after.map(f => f[1]))),
    over50: after.filter(f => f[1] > 50).length,
    scriptMs: Math.round(script), styleMs: Math.round(style), restMs: Math.round(longMs - script - style),
  })
})()`).then(JSON.parse)

const series = async (label) => {
  const runs = []
  for (let i = 0; i < warm + picks; i++) {
    const run = await pick()
    if (run.error) { console.log(run.error); break }
    if (i >= warm) {
      runs.push(run)
      if (process.env.EACH) console.log(`pick ${runs.length}: first answer ${run.firstMs} ms, marked ${run.markedMs} ms, player ${run.playerMs} ms, lost ${run.lostMs} ms, worst frame ${run.worstMs} ms, ${run.over50} over 50 ms`)
    }
    await sleep(3200)
  }
  if (!runs.length) return
  const values = key => runs.map(r => r[key]).filter(v => v !== null && v !== undefined)
  const mean = key => { const v = values(key); return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length) : '-' }
  const median = key => { const v = values(key).sort((a, b) => a - b); return v.length ? v[Math.floor(v.length / 2)] : '-' }
  const both = key => `${mean(key)} / ${median(key)}`
  console.log(`${label}, ${runs.length} picks (mean / median): first answer ${both('firstMs')} ms, card marked ${both('markedMs')} ms, player shows it ${both('playerMs')} ms`)
  console.log(`  frame time lost ${both('lostMs')} ms, worst frame ${both('worstMs')} ms, over 50 ms ${(runs.reduce((a, r) => a + r.over50, 0) / runs.length).toFixed(1)} a pick; long frames: script ${mean('scriptMs')} + style and layout ${mean('styleMs')} + rest ${mean('restMs')} ms`)
}
const setCss = variant => p.evaluate(`(() => {
  const variant = ${JSON.stringify(typeof variant === 'string' ? { css: variant } : variant || {})}
  window.__plairArt?.set({ coarse: null, lit: true, ...variant.art })
  window.__plairScene?.set({ skip: false, ...variant.scene })
  document.getElementById('catalogpick-css')?.remove()
  const css = variant.css || ''
  if (css) { const style = document.createElement('style'); style.id = 'catalogpick-css'; style.textContent = css; document.head.appendChild(style) }
  return 1
})()`)
if (process.env.VARIANTS) {
  const variants = Object.entries(JSON.parse(process.env.VARIANTS))
  try {
    for (let round = 0; round < Number(process.env.ROUNDS || 2); round++) {
      for (const [label, css] of round % 2 ? [...variants].reverse() : variants) {
        await setCss(css)
        await sleep(600)
        await series(label)
      }
    }
  } finally {
    await setCss('')
  }
} else {
  await series('Catalog')
}
p.close()
process.exit(0)
