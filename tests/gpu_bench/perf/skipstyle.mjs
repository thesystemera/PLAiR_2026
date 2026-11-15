// node skipstyle.mjs [min ms] [seconds] — traces one press of Next on the phone and lists every style recalculation
// and layout over [min ms] (time after the press, length, elements touched) with what invalidated styles before it
// (reason, node, changed attribute or selector). It changes the owner's song.
import { page, sleep } from './cdp.mjs'

const minMs = Number(process.argv[2] || 8)
const seconds = Number(process.argv[3] || 1.6)
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
await send('Tracing.start', { categories: 'devtools.timeline,disabled-by-default-devtools.timeline,disabled-by-default-devtools.timeline.invalidationTracking,blink.user_timing', transferMode: 'ReportEvents' })
await sleep(400)
await p.evaluate(`(() => { performance.mark('skip-pressed'); document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' })); return 1 })()`)
await sleep(seconds * 1000)
const complete = new Promise(r => { done = r })
await send('Tracing.end')
await complete

const mark = events.find(e => e.name === 'skip-pressed')
if (!mark) { console.log('the press was not found in the trace'); process.exit(1) }
const mine = events.filter(e => e.pid === mark.pid && e.tid === mark.tid && e.ts >= mark.ts - 2000).sort((a, b) => a.ts - b.ts)
const at = e => ((e.ts - mark.ts) / 1000).toFixed(0).padStart(5)
let invalidations = []
let styleMs = 0
let layoutMs = 0
const describe = e => {
  const d = e.args?.data || {}
  const what = [d.reason, d.nodeName, d.changedAttribute && `attr ${d.changedAttribute}`, d.changedClass && `class ${d.changedClass}`, d.changedId && `id ${d.changedId}`, d.changedPseudo && `pseudo ${d.changedPseudo}`, d.extraData].filter(Boolean).join(' | ')
  return what.slice(0, 150)
}
for (const e of mine) {
  if (e.name === 'StyleRecalcInvalidationTracking' || e.name === 'StyleInvalidatorInvalidationTracking' || e.name === 'ScheduleStyleInvalidationTracking') {
    invalidations.push(describe(e))
    continue
  }
  if (e.ph !== 'X') continue
  const ms = (e.dur || 0) / 1000
  if (e.name === 'UpdateLayoutTree') {
    styleMs += ms
    if (ms >= minMs) {
      const counts = new Map()
      for (const text of invalidations) counts.set(text, (counts.get(text) || 0) + 1)
      const top = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([text, n]) => `${n}x ${text}`)
      console.log(`${at(e)} ms: style ${ms.toFixed(0)} ms, ${e.args?.elementCount ?? '?'} elements${e.args?.beginData?.stackTrace ? ' (forced by script)' : ''}`)
      for (const line of top) console.log(`          ${line}`)
    }
    invalidations = []
  } else if (e.name === 'Layout') {
    layoutMs += ms
    if (ms >= minMs) console.log(`${at(e)} ms: layout ${ms.toFixed(0)} ms, ${e.args?.beginData?.dirtyObjects ?? '?'} of ${e.args?.beginData?.totalObjects ?? '?'} objects${e.args?.beginData?.stackTrace ? ' (forced by script)' : ''}`)
  }
}
console.log(`total in ${seconds} s after the press: style ${styleMs.toFixed(0)} ms, layout ${layoutMs.toFixed(0)} ms`)
browser.close()
p.close()
process.exit(0)
