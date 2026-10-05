import { page, sleep } from './cdp.mjs'

const variants = JSON.parse(process.argv[2] || '[["with copy",{"skipCopy":false}],["no copy",{"skipCopy":true}]]')
const rounds = Number(process.argv[3] || 8)
const panel = process.argv[4] || 'Playing'
const t0 = Date.now()
const stamp = () => ((Date.now() - t0) / 1000).toFixed(1) + 's ' + new Date().toTimeString().slice(0, 8)
const out = []
const say = (...args) => { const line = [stamp(), ...args].join(' '); out.push(line); console.log(line) }

const HOOK = `(() => {
  const events = window.__ctxEvents = []
  let made = 0
  const watch = (canvas, kind) => {
    if (canvas.__ctxWatched) return
    canvas.__ctxWatched = true
    const size = () => canvas.width + 'x' + canvas.height
    canvas.addEventListener('webglcontextlost', e => events.push({ t: performance.now(), type: 'lost', kind, size: size(), msg: e.statusMessage || '' }))
    canvas.addEventListener('webglcontextrestored', () => events.push({ t: performance.now(), type: 'restored', kind, size: size() }))
    canvas.addEventListener('webglcontextcreationerror', e => events.push({ t: performance.now(), type: 'creationerror', kind, msg: e.statusMessage || '' }))
  }
  for (const Proto of [HTMLCanvasElement.prototype, typeof OffscreenCanvas !== 'undefined' ? OffscreenCanvas.prototype : null]) {
    if (!Proto) continue
    const original = Proto.getContext
    Proto.getContext = function (type, options) {
      const gl = original.call(this, type, options)
      if (/webgl/.test(type)) {
        made++
        events.push({ t: performance.now(), type: gl ? 'created' : 'failed', kind: type, n: made, size: this.width + 'x' + this.height, pdb: !!options?.preserveDrawingBuffer })
        if (gl) watch(this, type)
      }
      return gl
    }
  }
})()`

const p = await page()
const consoleLines = []
await p.send('Runtime.enable')
await p.send('Log.enable')
await p.send('Page.enable')
p.on(m => {
  if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type)) {
    consoleLines.push(stamp() + ' console.' + m.params.type + ' ' + m.params.args.map(a => a.value || a.description || '').join(' ').slice(0, 300))
  }
  if (m.method === 'Log.entryAdded') consoleLines.push(stamp() + ' log.' + m.params.entry.level + ' ' + m.params.entry.source + ' ' + (m.params.entry.text || '').slice(0, 300))
  if (m.method === 'Runtime.exceptionThrown') consoleLines.push(stamp() + ' EXC ' + JSON.stringify(m.params.exceptionDetails).slice(0, 300))
})
const { identifier } = await p.send('Page.addScriptToEvaluateOnNewDocument', { source: HOOK }).then(r => r.result || {})

const state = () => p.evaluate(`(async () => {
  const art = window.__plairArt?.stats()
  let scene = null
  try { scene = await Promise.race([window.__plairScene?.eval('return scene.renderer.getContext().isContextLost()'), new Promise(r => setTimeout(() => r('timeout'), 800))]) } catch (e) { scene = 'err ' + e.message }
  return JSON.stringify({ sceneLost: scene, artMB: art?.textureMB, atlas: art?.atlas, views: art?.views, events: (window.__ctxEvents || []).filter(e => e.type !== 'created').map(e => e.type + ':' + e.kind + '@' + Math.round(e.t) + (e.msg ? ' ' + e.msg : '')), made: (window.__ctxEvents || []).filter(e => e.type === 'created').length })
})()`)

say('reload')
await p.send('Page.reload', { ignoreCache: true })
await sleep(11000)
say('after load', await state())
await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(panel)}).click(), 1`)
let wobbling = true
const wobble = (async () => { const start = Date.now(); while (wobbling) { const t = (Date.now() - start) / 1000; await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 10 * Math.sin(t * Math.PI * 1.6), gamma: 10 * Math.sin(t * Math.PI * 1.2 + 1) }); await sleep(33) } })()
await sleep(1500)
const measure = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1)) }; requestAnimationFrame(tick) })`)
let lost = false
for (let r = 0; r < rounds && !lost; r++) {
  for (const [label, set] of variants) {
    const { lightOff, ...art } = set
    await p.evaluate(`window.__plairArt.set(${JSON.stringify(art)}), window.__plairLight.debug({ off: ${!!lightOff} }), 1`)
    await sleep(700)
    const fps = await measure(3000)
    const s = await state()
    say(`round ${r + 1} ${label.padEnd(12)} fps ${fps}`, s)
    if (/"sceneLost":(true|"timeout")/.test(s) || /"artMB":0[,}]/.test(s) || (s.match(/lost:/g) || []).length > 1) { lost = true; break }
  }
}
wobbling = false
await wobble
await p.send('DeviceOrientation.clearDeviceOrientationOverride')
await p.evaluate(`window.__plairArt?.set({ skipCopy: false }), window.__plairLight?.debug({ off: false }), 1`)
if (identifier) await p.send('Page.removeScriptToEvaluateOnNewDocument', { identifier })
const events = await p.evaluate('JSON.stringify(window.__ctxEvents || [])')
say(lost ? 'LOST' : 'survived', 'context events', events)
for (const line of consoleLines) out.push(line)
console.log(consoleLines.join('\n'))
const fs = await import('node:fs')
fs.appendFileSync('ctxloss_log.txt', out.join('\n') + '\n\n')
p.close()
process.exit(0)
