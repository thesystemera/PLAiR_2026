// node uploadtime.mjs [rounds] [distance] [speed] - times every WebGL texture upload (texImage2D / texSubImage2D) on the
// phone's page while the Catalog is flung, measured around each call (not a profiler, which inflates native calls):
// calls, total and worst ms per second of scrolling, split by canvas and by source size. ART='<json>' applies cover
// switches for the run. Wrappers are removed at the end.
import { page, sleep, openPanel } from './cdp.mjs'

const rounds = Number(process.argv[2] || 2)
const distance = Number(process.argv[3] || 1800)
const speed = Number(process.argv[4] || 2500)
const p = await page()
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
await openPanel(p, 'Catalog')
if (process.env.ART) await p.evaluate(`window.__plairArt.set(${process.env.ART}), 1`)
await sleep(1200)
await p.evaluate(`(() => {
  const stats = window.__uploadStats = new Map()
  window.__uploadOriginals = []
  for (const proto of [WebGL2RenderingContext.prototype, WebGLRenderingContext.prototype]) {
    for (const name of ['texImage2D', 'texSubImage2D']) {
      const original = proto[name]
      window.__uploadOriginals.push([proto, name, original])
      proto[name] = function (...args) {
        const start = performance.now()
        const result = original.apply(this, args)
        const ms = performance.now() - start
        const source = args.find(a => a && typeof a === 'object' && 'width' in a && 'height' in a)
        const size = source ? source.width + 'x' + source.height + ' ' + (source.constructor?.name || '') : (args.length > 6 ? args[3] + 'x' + args[4] + ' data' : '?')
        const key = name + ' | canvas ' + this.canvas.width + 'x' + this.canvas.height + ' | ' + size
        const entry = stats.get(key) || { n: 0, ms: 0, worst: 0 }
        entry.n++; entry.ms += ms; entry.worst = Math.max(entry.worst, ms)
        stats.set(key, entry)
        return result
      }
    }
  }
  return 1
})()`)
const started = Date.now()
for (let i = 0; i < rounds; i++) {
  for (const direction of [-1, 1]) {
    const r = await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
    if (r.error) throw new Error(r.error.message)
    await sleep(250)
  }
}
const seconds = (Date.now() - started) / 1000
const out = JSON.parse(await p.evaluate(`(() => {
  for (const [proto, name, original] of window.__uploadOriginals) proto[name] = original
  const rows = [...window.__uploadStats].map(([key, e]) => ({ key, ...e }))
  delete window.__uploadStats; delete window.__uploadOriginals
  return JSON.stringify(rows)
})()`))
if (process.env.ART) await p.evaluate(`window.__plairArt.set({ skipDraw: false, skipCopy: false, lit: true, coarse: null }), 1`)
const total = out.reduce((a, r) => a + r.ms, 0)
console.log(`${seconds.toFixed(1)} s of flinging: ${out.reduce((a, r) => a + r.n, 0)} uploads, ${(total / seconds).toFixed(0)} ms/s on the main thread`)
for (const r of out.sort((a, b) => b.ms - a.ms).slice(0, 10)) console.log(`${(r.ms / seconds).toFixed(1).padStart(7)} ms/s  ${(r.n / seconds).toFixed(1).padStart(5)}/s  avg ${(r.ms / r.n).toFixed(2)} ms  worst ${r.worst.toFixed(1)} ms  ${r.key}`)
p.close()
process.exit(0)
