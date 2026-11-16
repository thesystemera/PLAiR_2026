// node skipseries.mjs [skips] [panel] [warm-up skips] — presses Next several times on the phone (6 s apart) and prints,
// per skip and on average: frame time lost in the 1.5 s after the press, worst frame, frames over 50 ms, and the
// long-animation-frame scripts summed by source (ms per skip). Single skips are noisy; compare series. It changes the
// owner's song once per skip. CPU=6 slows the page's CPU six times (for a desktop Chrome standing in for a phone).
import { page, sleep, openPanel, closeDialogs } from './cdp.mjs'

const skips = Number(process.argv[2] || 5)
const panel = process.argv[3] || 'Playing'
const warm = Number(process.argv[4] || 1)
const WINDOW_MS = 1500
const p = await page()
if (process.env.CPU) {
  await p.send('Emulation.setCPUThrottlingRate', { rate: Number(process.env.CPU) })
  await closeDialogs(p)
}
await openPanel(p, panel)
await sleep(1500)
await p.evaluate(`(() => {
  if (window.__skipSeries) return 1
  const watch = window.__skipSeries = { long: [] }
  new PerformanceObserver(list => {
    for (const e of list.getEntries()) {
      watch.long.push({ at: e.startTime, ms: e.duration, style: e.styleAndLayoutStart ? e.startTime + e.duration - e.styleAndLayoutStart : 0, scripts: e.scripts.map(s => ({ ms: s.duration, forced: s.forcedStyleAndLayoutDuration, by: (s.invoker || '').slice(0, 44), at: s.sourceCharPosition })) })
    }
  }).observe({ type: 'long-animation-frame' })
  return 1
})()`)
const press = () => p.evaluate(`(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  const frames = []
  let last = performance.now()
  let running = true
  const tick = t => { frames.push([t, t - last]); last = t; if (running) requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
  await sleep(600)
  const button = document.querySelector('button[title="Next track"]')
  if (!button) { running = false; return JSON.stringify({ error: 'no Next button on screen' }) }
  window.__skipSeries.long.length = 0
  const pressed = performance.now()
  button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' }))
  await sleep(${WINDOW_MS + 300})
  running = false
  const after = frames.filter(f => f[0] >= pressed && f[0] <= pressed + ${WINDOW_MS})
  const sources = {}
  let forced = 0
  let style = 0
  let scripts = 0
  let longMs = 0
  const frameList = []
  for (const frame of window.__skipSeries.long) {
    if (frame.at + frame.ms < pressed || frame.at > pressed + ${WINDOW_MS}) continue
    style += frame.style
    longMs += frame.ms
    let own = 0
    for (const s of frame.scripts) {
      const key = s.by + ' @' + s.at
      sources[key] = (sources[key] || 0) + s.ms
      forced += s.forced
      own += s.ms
    }
    scripts += own
    frameList.push([Math.round(frame.at - pressed), Math.round(frame.ms), Math.round(own), Math.round(frame.style), Math.round(frame.ms - own - frame.style)])
  }
  return JSON.stringify({
    lostMs: Math.round(after.filter(f => f[1] > 25).reduce((a, f) => a + f[1] - 16.7, 0)),
    worstMs: Math.round(Math.max(...after.map(f => f[1]))),
    over50: after.filter(f => f[1] > 50).length,
    forcedStyleMs: Math.round(forced), frameStyleMs: Math.round(style), sources,
    scriptMs: Math.round(scripts), longMs: Math.round(longMs), restMs: Math.round(longMs - scripts - style), frameList,
    slow: after.filter(f => f[1] > 25).map(f => [Math.round(f[0] - pressed), Math.round(f[1])]),
  })
})()`).then(JSON.parse)

const runs = []
for (let i = 0; i < warm + skips; i++) {
  const run = await press()
  if (run.error) { console.log(run.error); break }
  if (i >= warm) {
    runs.push(run)
    console.log(`skip ${runs.length}: lost ${run.lostMs} ms, worst frame ${run.worstMs} ms, ${run.over50} over 50 ms, forced style and layout ${run.forcedStyleMs} ms, end-of-frame style and layout ${run.frameStyleMs} ms`)
    if (process.env.FRAMES) {
      console.log('   long frames (start: length = script + style/layout + rest):', run.frameList.map(f => `${f[0]}: ${f[1]} = ${f[2]} + ${f[3]} + ${f[4]}`).join(' | '))
      console.log('   slow frame gaps (end: length):', run.slow.map(f => `${f[0]}: ${f[1]}`).join(', '))
    }
  }
  await sleep(4000)
}
if (runs.length) {
  const mean = key => Math.round(runs.reduce((a, r) => a + r[key], 0) / runs.length)
  const median = key => [...runs.map(r => r[key])].sort((a, b) => a - b)[Math.floor(runs.length / 2)]
  console.log(`${panel}, ${runs.length} skips: lost ${mean('lostMs')} ms mean / ${median('lostMs')} median, worst frame ${mean('worstMs')} mean, over 50 ms ${(runs.reduce((a, r) => a + r.over50, 0) / runs.length).toFixed(1)} a skip, forced style and layout ${mean('forcedStyleMs')} ms, end-of-frame style and layout ${mean('frameStyleMs')} ms`)
  const totals = {}
  for (const run of runs) for (const [key, ms] of Object.entries(run.sources)) totals[key] = (totals[key] || 0) + ms
  console.log(`  long frames ${mean('longMs')} ms a skip = script ${mean('scriptMs')} + end-of-frame style and layout ${mean('frameStyleMs')} + rest (paint, commit, waiting) ${mean('restMs')}`)
  for (const [key, ms] of Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 14)) console.log(`  ${(ms / runs.length).toFixed(0).padStart(4)} ms a skip  ${key}`)
}
p.close()
process.exit(0)
