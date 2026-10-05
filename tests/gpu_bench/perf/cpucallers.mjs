import { page, sleep } from './cdp.mjs'
import { readFileSync, readdirSync } from 'node:fs'
const p = await page()
await p.send('Profiler.enable')
await p.send('Profiler.setSamplingInterval', { interval: 500 })
await p.send('Profiler.start')
await p.evaluate(`(() => { document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' })); return 1 })()`)
await sleep(1500)
const { result } = await p.send('Profiler.stop')
const prof = result.profile
const byId = new Map(prof.nodes.map(n => [n.id, n]))
const parent = new Map()
for (const n of prof.nodes) for (const c of n.children || []) parent.set(c, n.id)
const self = new Map()
for (let i = 0; i < prof.samples.length; i++) self.set(prof.samples[i], (self.get(prof.samples[i]) || 0) + (prof.timeDeltas[i] || 0))
const dist = 'E:/AI_RADIO/client/dist/assets/'
const files = Object.fromEntries(readdirSync(dist).filter(f => f.endsWith('.js')).map(f => [f, readFileSync(dist + f, 'utf8').split('\n')]))
const snippet = cf => { const f = (cf.url || '').split('/').pop(); const lines = files[f]; if (!lines) return f; const line = lines[cf.lineNumber] || ''; return f.slice(0, 18) + ' ' + JSON.stringify(line.slice(Math.max(0, cf.columnNumber - 60), cf.columnNumber + 60)) }
const callers = {}
for (const [id, us] of self) {
  const name = byId.get(id).callFrame.functionName
  if (!/clientHeight|checkVisibility|offsetWidth|getBoundingClientRect|offsetTop|offsetHeight/.test(name)) continue
  let pid = parent.get(id)
  const chain = []
  while (pid && chain.length < 2) { const cf = byId.get(pid).callFrame; if (cf.url) chain.push(snippet(cf)); pid = parent.get(pid) }
  const key = name + '  <=  ' + chain.join('  <=  ')
  callers[key] = (callers[key] || 0) + us
}
for (const [k, us] of Object.entries(callers).sort((a, b) => b[1] - a[1]).slice(0, 12)) console.log(Math.round(us / 1000) + 'ms ' + k + '\n')
p.close(); process.exit(0)
