// node skipwatch.mjs [seconds] — one press of Next on the phone (the Next button must be on screen, so not in fullscreen
// visuals), then: slow frames after the press, the background scene's own steps (cover handed over, smooth copies,
// cover swapped in, features set) and frames, and every long animation frame with the scripts that ran in it.
// A script's `@position` is its character offset in the built `client/dist/assets/index-*.js`: open the file there to
// see what it is. The recorders stay installed until the page reloads; they change nothing.
import { page, sleep } from './cdp.mjs'

const seconds = Number(process.argv[2] || 6)
const p = await page()
const run = code => p.evaluate(`window.__plairScene.eval(${JSON.stringify(code)})`)
console.log(await run(`
  if (window.__skipWatch) {
    for (const list of Object.values(window.__skipWatch)) list.length = 0
    return 'recorders ready'
  }
  const watch = window.__skipWatch = { long: [], steps: [], frames: [] }
  new PerformanceObserver(list => {
    for (const e of list.getEntries()) {
      if (e.duration < 40) continue
      watch.long.push({ at: Math.round(e.startTime), ms: Math.round(e.duration), blocked: Math.round(e.blockingDuration), scripts: e.scripts.filter(s => s.duration > 4).map(s => ({ ms: Math.round(s.duration), by: (s.invoker || '').slice(0, 50), fn: (s.sourceFunctionName || '').slice(0, 24), pos: s.sourceCharPosition })) })
    }
  }).observe({ type: 'long-animation-frame' })
  const time = (owner, name, label) => {
    const original = owner[name].bind(owner)
    owner[name] = (...args) => { const t = performance.now(); const out = original(...args); watch.steps.push({ at: Math.round(t), what: label, ms: +(performance.now() - t).toFixed(1) }); return out }
  }
  time(scene, 'setFeatures', 'features set')
  time(scene.softener, 'soften', 'smooth copies of the cover')
  time(scene, 'setArtwork', 'cover handed to the scene')
  time(scene, 'applyLayers', 'cover swapped in')
  const frame = scene.frame.bind(scene)
  const render = scene.renderer.render.bind(scene.renderer)
  let rendering = 0
  scene.renderer.render = (a, b) => { const t = performance.now(); render(a, b); rendering += performance.now() - t }
  scene.frame = delta => { rendering = 0; const t = performance.now(); frame(delta); const ms = performance.now() - t; if (ms > 6) watch.frames.push({ at: Math.round(t), ms: +ms.toFixed(1), render: +rendering.toFixed(1) }) }
  return 'recorders installed'`))
const result = JSON.parse(await p.evaluate(`(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  const frames = []
  let last = performance.now()
  let running = true
  const tick = t => { frames.push([Math.round(t), +(t - last).toFixed(1)]); last = t; if (running) requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
  await sleep(1200)
  const button = document.querySelector('button[title="Next track"]')
  if (!button) { running = false; return JSON.stringify({ error: 'no Next button on screen' }) }
  const pressed = Math.round(performance.now())
  button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' }))
  await sleep(${seconds * 1000})
  running = false
  const after = frames.filter(f => f[0] >= pressed)
  const slow = after.filter(f => f[1] > 25)
  return JSON.stringify({ pressed, frames: after.length, over25ms: slow.length, over50ms: after.filter(f => f[1] > 50).length, worstMs: Math.max(...after.map(f => f[1])), lostMs: Math.round(slow.reduce((a, f) => a + f[1] - 16.7, 0)), slow: slow.map(f => [f[0] - pressed, f[1]]) })
})()`))
if (result.error) {
  console.log(result.error)
} else {
  await sleep(300)
  const watch = JSON.parse(await run(`return JSON.stringify(window.__skipWatch)`))
  const { pressed, slow, ...summary } = result
  console.log('after the press:', JSON.stringify(summary))
  console.log('slow frames (ms after the press: length):', slow.map(([at, ms]) => `${at}: ${ms}`).join(', '))
  console.log('scene steps:', watch.steps.map(s => `${s.at - pressed}: ${s.what} ${s.ms} ms`).join(' | '))
  console.log('scene frames over 6 ms:', watch.frames.map(f => `${f.at - pressed}: ${f.ms} ms (render ${f.render})`).join(' | ') || 'none')
  for (const e of watch.long) console.log(`${e.at - pressed}: frame ${e.ms} ms, blocked ${e.blocked}:`, e.scripts.map(s => `${s.ms} ms ${s.by} ${s.fn}@${s.pos}`).join('; '))
}
p.close()
process.exit(0)
