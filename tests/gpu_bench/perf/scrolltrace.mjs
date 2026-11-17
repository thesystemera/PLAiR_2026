// node scrolltrace.mjs [panel] [distance px] [speed px/s] [rounds] — trace of the phone while the panel is scrolled
// down and back with a synthetic finger: per thread (page main, compositor, raster workers, GPU process) the busy time
// per second, and for the page's main thread the self time by kind of work (style, layout, paint, script...), with the
// layouts and style recalculations that script forced counted apart. PICK=1 traces one tap on a Catalog card instead
// of a scroll (it changes the owner's song). ART='<json>' / SCENE='<json>' apply cover / scene switches for the run
// (window.__plairArt.set, window.__plairScene.set); JS / UNDO run page code before / after; e.g. ART='{"skipDraw":true}' SCENE='{"skip":true}' for the floor.
// THREADS=CrGpuMain,CompositorGpuThread,... prints those threads' self time by event (TOP=n rows) and the page's and
// the display's frames per second; CATS adds trace categories (e.g. toplevel,gpu.service). PAINTS=1 sums the page's
// painted area per second by element (PAINTS=dump also prints a few raw Paint events). INVAL=1 counts paint
// invalidations by reason and element (adds the invalidationTracking category itself). COUNT='<regex>' counts trace
// events by name per second (DUMPARGS='<name part>' prints the args of the first two).
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Catalog'
const distance = Number(process.argv[3] || 1800)
const speed = Number(process.argv[4] || 2500)
const rounds = Number(process.argv[5] || 2)
const version = await (await fetch(`http://127.0.0.1:${process.env.CDP_PORT || 9222}/json/version`)).json()
const browser = new WebSocket(version.webSocketDebuggerUrl)
await new Promise(r => browser.addEventListener('open', r, { once: true }))
let id = 0
const pending = new Map()
const events = []
let done = null
browser.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Tracing.dataCollected') events.push(...m.params.value)
  if (m.method === 'Tracing.tracingComplete') done?.()
})
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); browser.send(JSON.stringify({ id: i, method, params })) })

const p = await page()
await openPanel(p, panel)
if (process.env.ART) await p.evaluate(`window.__plairArt.set(${process.env.ART}), 1`)
if (process.env.SCENE) await p.evaluate(`window.__plairScene.set(${process.env.SCENE}), 1`)
if (process.env.JS) console.log('JS:', await p.evaluate(process.env.JS))
await sleep(2000)
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
await send('Tracing.start', { categories: 'devtools.timeline,disabled-by-default-devtools.timeline,disabled-by-default-devtools.timeline.stack,blink.user_timing,gpu,viz,cc' + (process.env.CATS ? ',' + process.env.CATS : '') + (process.env.INVAL ? ',disabled-by-default-devtools.timeline.invalidationTracking' : ''), transferMode: 'ReportEvents' })
await sleep(300)
await p.evaluate(`performance.mark('trace-from'), 1`)
if (process.env.PICK) {
  await p.evaluate(`(() => {
    const cards = [...document.querySelectorAll('[class*="group/card"]')].filter(card => { const r = card.getBoundingClientRect(); return r.width > 40 && r.top > 110 && r.bottom < innerHeight - 200 && !/ui-current-outline|ring-2/.test(card.className) })
    const card = cards[Math.floor(Math.random() * cards.length)]
    const r = card.getBoundingClientRect()
    const at = { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 3, pointerId: 1, pointerType: 'touch', isPrimary: true }
    card.dispatchEvent(new PointerEvent('pointerdown', at))
    card.dispatchEvent(new PointerEvent('pointerup', at))
    return 1
  })()`)
  await sleep(1500)
} else {
  for (let i = 0; i < rounds; i++) {
    for (const direction of [-1, 1]) {
      await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
      await sleep(250)
    }
  }
}
await p.evaluate(`performance.mark('trace-to'), 1`)
const complete = new Promise(r => { done = r })
await send('Tracing.end')
await complete

const from = events.find(e => e.name === 'trace-from')
const to = events.find(e => e.name === 'trace-to')
if (!from || !to) { console.log('the marks were not found in the trace'); process.exit(1) }
const seconds = (to.ts - from.ts) / 1e6
const threadNames = new Map()
for (const e of events) if (e.name === 'thread_name') threadNames.set(`${e.pid}:${e.tid}`, e.args?.name || '')
const byThread = new Map()
for (const e of events) {
  if (e.ph !== 'X' || e.ts < from.ts || e.ts > to.ts) continue
  const key = `${e.pid}:${e.tid}`
  if (!byThread.has(key)) byThread.set(key, [])
  byThread.get(key).push(e)
}
const selfTimes = list => {
  list.sort((a, b) => a.ts - b.ts || (b.dur || 0) - (a.dur || 0))
  const self = new Map()
  const stack = []
  let busy = 0
  const add = (name, us) => self.set(name, (self.get(name) || 0) + us)
  for (const e of list) {
    const end = e.ts + (e.dur || 0)
    while (stack.length && stack[stack.length - 1].end <= e.ts) stack.pop()
    if (stack.length) add(stack[stack.length - 1].name, -Math.min(e.dur || 0, stack[stack.length - 1].end - e.ts))
    else busy += e.dur || 0
    add(e.name, e.dur || 0)
    stack.push({ name: e.name, end })
  }
  return { self, busy }
}
const mainKey = `${from.pid}:${from.tid}`
console.log(`${panel}: ${seconds.toFixed(1)} s traced (${process.env.PICK ? 'one pick' : 'scrolling'})`)
const rows = []
for (const [key, list] of byThread) {
  const { busy } = selfTimes(list)
  rows.push([busy / 1000 / seconds, `${threadNames.get(key) || key}${key === mainKey ? ' (the page)' : ''}`])
}
for (const [ms, name] of rows.sort((a, b) => b[0] - a[0]).slice(0, 9)) console.log(`${ms.toFixed(0).padStart(5)} ms/s busy  ${name}`)
const main = byThread.get(mainKey) || []
const { self } = selfTimes(main)
console.log('the page\'s main thread, self time by kind of work:')
for (const [name, us] of [...self].sort((a, b) => b[1] - a[1]).slice(0, 16)) console.log(`${(us / 1000 / seconds).toFixed(0).padStart(5)} ms/s  ${name}`)
if (process.env.THREADS) {
  const count = name => events.filter(e => e.name === name && e.ts >= from.ts && e.ts <= to.ts).length / seconds
  console.log(`frames per second: page BeginMainFrame ${count('ProxyMain::BeginMainFrame').toFixed(1)}, display swaps ${count('Display::DrawAndSwap').toFixed(1)}`)
  const wanted = process.env.THREADS.split(',')
  for (const [key, list] of byThread) {
    const name = threadNames.get(key) || key
    if (!wanted.some(w => name.startsWith(w))) continue
    const { self, busy } = selfTimes(list)
    const counts = new Map()
    for (const e of list) counts.set(e.name, (counts.get(e.name) || 0) + 1)
    console.log(`${name} (${(busy / 1000 / seconds).toFixed(0)} ms/s busy), self time by event:`)
    for (const [event, us] of [...self].sort((a, b) => b[1] - a[1]).slice(0, Number(process.env.TOP || 12))) {
      console.log(`${(us / 1000 / seconds).toFixed(1).padStart(7)} ms/s  ${(counts.get(event) / seconds).toFixed(0).padStart(5)}/s  ${event}`)
    }
  }
}
if (process.env.PAINTS) {
  const paints = main.filter(e => e.name === 'Paint' && e.args?.data)
  const area = q => (q && q.length >= 8 ? Math.abs((q[2] - q[0]) * (q[5] - q[1])) : 0)
  const byNode = new Map()
  let total = 0
  for (const e of paints) {
    const a = area(e.args.data.clip)
    total += a
    const key = e.args.data.nodeName || e.args.data.nodeId || e.args.data.layerId || '?'
    const entry = byNode.get(key) || { area: 0, n: 0, ms: 0, w: 0, h: 0 }
    entry.area += a; entry.n++; entry.ms += (e.dur || 0) / 1000
    const q = e.args.data.clip
    if (q && q.length >= 8) { entry.w = Math.round(Math.abs(q[2] - q[0])); entry.h = Math.round(Math.abs(q[5] - q[1])) }
    byNode.set(key, entry)
  }
  console.log(`paints: ${(paints.length / seconds).toFixed(0)}/s, painted area ${(total / 1e6 / seconds).toFixed(1)} Mpx/s (screen is ${(1080 * 2260 / 1e6).toFixed(1)} Mpx)`)
  for (const [key, e] of [...byNode].sort((a, b) => b[1].area - a[1].area).slice(0, 8)) console.log(`  ${(e.area / 1e6 / seconds).toFixed(2).padStart(6)} Mpx/s  ${(e.n / seconds).toFixed(0).padStart(4)}/s  ${e.ms.toFixed(0).padStart(4)} ms  last ${e.w}x${e.h}  ${key}`)
  if (process.env.PAINTS === 'dump') console.log(JSON.stringify(paints.slice(0, 3).map(e => e.args.data)))
}
if (process.env.INVAL) {
  const inval = events.filter(e => e.name === 'PaintInvalidationTracking' && e.ts >= from.ts && e.ts <= to.ts && e.args?.data)
  const byKey = new Map()
  for (const e of inval) {
    const d = e.args.data
    const key = `${d.reason || '?'} | ${(d.nodeName || '').slice(0, 80)}`
    byKey.set(key, (byKey.get(key) || 0) + 1)
  }
  console.log(`paint invalidations: ${(inval.length / seconds).toFixed(0)}/s; invalidation event names in the trace: ${[...new Set(events.filter(e => /nvalidat|Raster|Tile/.test(e.name)).map(e => e.name))].slice(0, 30).join(', ')}`)
  for (const [key, n] of [...byKey].sort((a, b) => b[1] - a[1]).slice(0, Number(process.env.TOP || 15))) console.log(`${(n / seconds).toFixed(0).padStart(6)}/s  ${key}`)
}
if (process.env.COUNT) {
  const re = new RegExp(process.env.COUNT)
  const counts = new Map()
  for (const e of events) if (re.test(e.name) && e.ts >= from.ts && e.ts <= to.ts) counts.set(e.name, (counts.get(e.name) || 0) + 1)
  for (const [name, n] of [...counts].sort((a, b) => b[1] - a[1])) console.log(`${(n / seconds).toFixed(1).padStart(8)}/s  ${name.slice(0, 120)}`)
  if (process.env.DUMPARGS) for (const e of events.filter(e => e.name.includes(process.env.DUMPARGS)).slice(0, 2)) console.log(JSON.stringify(e.args).slice(0, 1500))
}
const forced = main.filter(e => (e.name === 'Layout' || e.name === 'UpdateLayoutTree') && (e.args?.beginData?.stackTrace || e.args?.stackTrace))
const sum = (name, list) => list.filter(e => e.name === name).reduce((a, e) => a + (e.dur || 0), 0) / 1000
const all = name => main.filter(e => e.name === name)
console.log(`layouts: ${all('Layout').length} (${sum('Layout', main).toFixed(0)} ms), of them forced by script ${forced.filter(e => e.name === 'Layout').length} (${sum('Layout', forced).toFixed(0)} ms)`)
console.log(`style recalculations: ${all('UpdateLayoutTree').length} (${sum('UpdateLayoutTree', main).toFixed(0)} ms, ${all('UpdateLayoutTree').reduce((a, e) => a + (e.args?.elementCount || 0), 0)} elements), forced by script ${forced.filter(e => e.name === 'UpdateLayoutTree').length} (${sum('UpdateLayoutTree', forced).toFixed(0)} ms)`)
const forcedBy = new Map()
for (const e of forced) {
  const frame = (e.args.beginData?.stackTrace || e.args.stackTrace)[0]
  const key = `${frame?.functionName || '(anonymous)'} ${(frame?.url || '').split('/').pop().slice(0, 28)}:${frame?.columnNumber}`
  const entry = forcedBy.get(key) || { ms: 0, n: 0 }
  entry.ms += (e.dur || 0) / 1000
  entry.n++
  forcedBy.set(key, entry)
}
for (const [key, entry] of [...forcedBy].sort((a, b) => b[1].ms - a[1].ms).slice(0, 8)) console.log(`   forced ${entry.n}x, ${entry.ms.toFixed(0)} ms by ${key}`)
if (process.env.UNDO) await p.evaluate(process.env.UNDO)
if (process.env.ART) await p.evaluate(`window.__plairArt.set({ skipDraw: false, skipCopy: false, lit: true, coarse: null }), 1`)
if (process.env.SCENE) await p.evaluate(`window.__plairScene.set({ skip: false }), 1`)
browser.close()
p.close()
process.exit(0)
