// node scrollprof.mjs [panel] [distance px] [speed px/s] [rounds] — CPU profile of the phone's page while the panel is
// scrolled down and back with a synthetic finger: frame rate, then main-thread time per second of scrolling split into
// script, garbage collection and the rest (style, layout, paint), and self time by function with native calls charged
// to their callers and a snippet of the built file (needs client/dist to be the build the phone runs). Then the scripts
// inside the long frames by what started them, with the time each spent forcing style or layout. TOP=30 sets how
// many functions are listed. PICK=3 profiles three taps on Catalog cards (1.5 s each) instead of a scroll: it changes
// the owner's song. ART='<json>' / SCENE='<json>' apply cover / scene switches for the run (put back at the end).
import { readFileSync, readdirSync } from 'node:fs'
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Catalog'
const distance = Number(process.argv[3] || 1800)
const speed = Number(process.argv[4] || 2500)
const rounds = Number(process.argv[5] || 2)
const DIST = 'E:/AI_RADIO/client/dist/assets/'
const p = await page()
await openPanel(p, panel)
if (process.env.ART) await p.evaluate(`window.__plairArt.set(${process.env.ART}), 1`)
if (process.env.SCENE) await p.evaluate(`window.__plairScene.set(${process.env.SCENE}), 1`)
await sleep(2000)
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
await p.send('Profiler.enable')
await p.send('Profiler.setSamplingInterval', { interval: 250 })
await p.evaluate(`(() => {
  const frames = []
  let last = performance.now()
  let running = true
  const tick = t => { frames.push(t - last); last = t; if (running) requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
  const long = []
  const observer = new PerformanceObserver(list => { for (const e of list.getEntries()) for (const s of e.scripts) long.push({ ms: s.duration, forced: s.forcedStyleAndLayoutDuration, by: (s.invoker || '').slice(0, 46), at: s.sourceCharPosition, fn: s.sourceFunctionName }) })
  observer.observe({ type: 'long-animation-frame' })
  window.__scrollProf = { frames, long, stop: () => { running = false; observer.disconnect() } }
  return 1
})()`)
await sleep(300)
const started = Date.now()
await p.send('Profiler.start')
if (process.env.PICK) {
  for (let i = 0; i < Number(process.env.PICK); i++) {
    await p.evaluate(`(() => {
      const cards = [...document.querySelectorAll('[class*="group/card"]')].filter(card => { const r = card.getBoundingClientRect(); return r.width > 40 && r.top > 110 && r.bottom < innerHeight - 200 })
      const card = cards[${i} % cards.length]
      const r = card.getBoundingClientRect()
      const at = { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 3, pointerId: 1, pointerType: 'touch', isPrimary: true }
      card.dispatchEvent(new PointerEvent('pointerdown', at))
      card.dispatchEvent(new PointerEvent('pointerup', at))
      return 1
    })()`)
    await sleep(1500)
  }
} else {
  for (let i = 0; i < rounds; i++) {
    for (const direction of [-1, 1]) {
      await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
      await sleep(250)
    }
  }
}
const { result } = await p.send('Profiler.stop')
const seconds = (Date.now() - started) / 1000
const frames = JSON.parse(await p.evaluate(`(() => { const w = window.__scrollProf; w.stop(); const gaps = w.frames.slice(3); const total = gaps.reduce((a, b) => a + b, 0); return JSON.stringify({ fps: +(gaps.length / total * 1000).toFixed(1), worst: Math.round(Math.max(...gaps)), over25: gaps.filter(g => g > 25).length, count: gaps.length, long: w.long }) })()`))
console.log(`${panel}: ${seconds.toFixed(1)} s of ${process.env.PICK ? `${process.env.PICK} picks, 1.5 s each` : 'scrolling'}, ${frames.fps} fps, worst frame ${frames.worst} ms, ${frames.over25} of ${frames.count} frames over 25 ms`)

const profile = result.profile
const nodes = new Map(profile.nodes.map(node => [node.id, { ...node, self: 0 }]))
for (let i = 0; i < profile.samples.length; i++) nodes.get(profile.samples[i]).self += (profile.timeDeltas[i] || 0) / 1000
const sources = new Map()
const snippet = (url, line, column) => {
  const file = (url || '').split('/').pop().split('?')[0]
  if (!file) return ''
  if (!sources.has(file)) {
    let text = null
    try { if (readdirSync(DIST).includes(file)) text = readFileSync(DIST + file, 'utf8').split('\n') } catch { /* not built here */ }
    sources.set(file, text)
  }
  return sources.get(file)?.[line]?.slice(column, column + 90).replace(/\s+/g, ' ') || ''
}
const label = node => `${node.callFrame.functionName || '(anonymous)'} ${(node.callFrame.url || '').split('/').pop().slice(0, 24)}:${node.callFrame.columnNumber}`
const selfBy = new Map()
const totals = { script: 0, gc: 0, rest: 0, idle: 0 }
const walk = (node, owner) => {
  const frame = node.callFrame
  const name = frame.functionName
  if (name === '(idle)') totals.idle += node.self
  else if (name === '(garbage collector)') totals.gc += node.self
  else if (name === '(program)') totals.rest += node.self
  else if (name !== '(root)') totals.script += node.self
  const here = frame.url ? label(node) : owner
  const key = frame.url ? label(node) : `${name}  <- ${owner}`
  if (node.self > 0 && name !== '(idle)' && name !== '(root)') {
    const entry = selfBy.get(key) || { ms: 0, node }
    entry.ms += node.self
    selfBy.set(key, entry)
  }
  for (const child of node.children || []) walk(nodes.get(child), here)
}
walk(nodes.get(profile.nodes[0].id), '(top)')
const perSecond = ms => (ms / seconds).toFixed(0)
console.log(`main thread, ms per second: script ${perSecond(totals.script)}, garbage collection ${perSecond(totals.gc)}, the rest (style, layout, paint) ${perSecond(totals.rest)}, idle ${perSecond(totals.idle)}`)
for (const [key, entry] of [...selfBy].sort((a, b) => b[1].ms - a[1].ms).slice(0, Number(process.env.TOP || 30))) {
  const frame = entry.node.callFrame
  console.log(`${perSecond(entry.ms).padStart(5)} ms/s  ${key}  ${frame.url ? snippet(frame.url, frame.lineNumber, frame.columnNumber) : ''}`)
}
const scripts = new Map()
for (const s of frames.long) {
  const key = `${s.by} ${s.fn || ''} @${s.at}`
  const entry = scripts.get(key) || { ms: 0, forced: 0, n: 0 }
  entry.ms += s.ms
  entry.forced += s.forced
  entry.n++
  scripts.set(key, entry)
}
console.log('scripts in the long frames (over 50 ms), per second of scrolling:')
for (const [key, entry] of [...scripts].sort((a, b) => b[1].ms - a[1].ms).slice(0, 12)) console.log(`${perSecond(entry.ms).padStart(5)} ms/s (${perSecond(entry.forced)} forcing style or layout), ${entry.n}x  ${key}`)
if (process.env.ART) await p.evaluate(`window.__plairArt.set({ skipDraw: false, skipCopy: false, lit: true, coarse: null }), 1`)
if (process.env.SCENE) await p.evaluate(`window.__plairScene.set({ skip: false }), 1`)
p.close()
process.exit(0)
