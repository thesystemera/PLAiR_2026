import { page, sleep } from './cdp.mjs'
const p = await page()
await p.send('Profiler.enable')
await p.send('Profiler.setSamplingInterval', { interval: 500 })
await p.send('Profiler.start')
await p.evaluate(`(() => { document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' })); return 1 })()`)
await sleep(1500)
const { result } = await p.send('Profiler.stop')
const prof = result.profile
const byId = new Map(prof.nodes.map(n => [n.id, n]))
const self = new Map()
const dt = []
for (let i = 0; i < prof.samples.length; i++) self.set(prof.samples[i], (self.get(prof.samples[i]) || 0) + (prof.timeDeltas[i] || 0))
const agg = {}
let total = 0
for (const [id, us] of self) {
  const n = byId.get(id)
  const cf = n.callFrame
  if (cf.functionName === '(idle)' || cf.functionName === '(program)') continue
  total += us
  const file = (cf.url || '').split('/').pop()
  const key = (cf.functionName || '(anon)') + ' ' + file + ':' + cf.lineNumber + ':' + cf.columnNumber
  agg[key] = (agg[key] || 0) + us
}
const top = Object.entries(agg).sort((a, b) => b[1] - a[1]).slice(0, 30)
console.log('busy ms', Math.round(total / 1000), '| (garbage collector) ms', Math.round((agg[Object.keys(agg).find(k => k.startsWith('(garbage'))] || 0) / 1000))
for (const [k, us] of top) console.log(Math.round(us / 1000) + 'ms', k)
p.close(); process.exit(0)
