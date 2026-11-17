// node gltime.mjs [rounds] [distance] [speed] - times every WebGL call on the phone's page during Catalog flings and lists
// the ones that blocked longer than SLOW ms (default 3): by call and canvas size, how often, total and worst, plus the
// share of all WebGL time. Finds which call makes the page wait on Chrome's GPU process (WaitForToken in a trace).
// ART='<json>' / SCENE='<json>' apply cover / scene switches for the run. The wrappers are removed at the end.
import { page, sleep, openPanel } from './cdp.mjs'

const rounds = Number(process.argv[2] || 2)
const distance = Number(process.argv[3] || 1800)
const speed = Number(process.argv[4] || 2500)
const slow = Number(process.env.SLOW || 3)
const p = await page()
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
await openPanel(p, 'Catalog')
if (process.env.ART) await p.evaluate(`window.__plairArt.set(${process.env.ART}), 1`)
if (process.env.SCENE) await p.evaluate(`window.__plairScene.set(${process.env.SCENE}), 1`)
await sleep(1200)
await p.evaluate(`(() => {
  const stats = window.__glStats = { slow: new Map(), total: 0, calls: 0 }
  window.__glOriginals = []
  for (const proto of [WebGL2RenderingContext.prototype, WebGLRenderingContext.prototype]) {
    for (const name of Object.getOwnPropertyNames(proto)) {
      const descriptor = Object.getOwnPropertyDescriptor(proto, name)
      if (!descriptor || typeof descriptor.value !== 'function' || name === 'constructor') continue
      const original = descriptor.value
      window.__glOriginals.push([proto, name, original])
      proto[name] = function (...args) {
        const start = performance.now()
        const result = original.apply(this, args)
        const ms = performance.now() - start
        stats.total += ms
        stats.calls++
        if (ms > ${slow}) {
          const key = name + ' | canvas ' + this.canvas.width + 'x' + this.canvas.height
          const entry = stats.slow.get(key) || { n: 0, ms: 0, worst: 0 }
          entry.n++; entry.ms += ms; entry.worst = Math.max(entry.worst, ms)
          stats.slow.set(key, entry)
        }
        return result
      }
    }
  }
  return 1
})()`)
const started = Date.now()
try {
  for (let i = 0; i < rounds; i++) {
    for (const direction of [-1, 1]) {
      const r = await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
      if (r.error) throw new Error(r.error.message)
      await sleep(250)
    }
  }
} finally {
  const seconds = (Date.now() - started) / 1000
  const out = JSON.parse(await p.evaluate(`(() => {
    for (const [proto, name, original] of window.__glOriginals) proto[name] = original
    const s = window.__glStats
    const rows = [...s.slow].map(([key, e]) => ({ key, ...e }))
    delete window.__glStats; delete window.__glOriginals
    return JSON.stringify({ rows, total: s.total, calls: s.calls })
  })()`))
  if (process.env.ART) await p.evaluate(`window.__plairArt.set({ skipDraw: false, skipCopy: false, lit: true, coarse: null }), 1`)
  if (process.env.SCENE) await p.evaluate(`window.__plairScene.set({ skip: false }), 1`)
  console.log(`${seconds.toFixed(1)} s of flinging: ${out.calls} WebGL calls, ${(out.total / seconds).toFixed(0)} ms/s in them; calls over ${slow} ms:`)
  for (const r of out.rows.sort((a, b) => b.ms - a.ms).slice(0, 12)) console.log(`${(r.ms / seconds).toFixed(1).padStart(7)} ms/s  ${r.n}x  worst ${r.worst.toFixed(1)} ms  ${r.key}`)
}
p.close()
