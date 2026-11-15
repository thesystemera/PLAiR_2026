// node workerprof.mjs [seconds] [name part] — CPU profile of a worker of the phone's page (default: the share-video
// worker) while it runs: self time by function, native calls charged to their callers, with a snippet of the built
// file. Start the work first (e.g. sharebench.mjs with a long clip), then run this.
import { readFileSync, readdirSync } from 'node:fs'

const seconds = Number(process.argv[2] || 5)
const wanted = process.argv[3] || 'shareVideoWorker'
const DIST = 'E:/AI_RADIO/client/dist/assets/'
const sleep = ms => new Promise(r => setTimeout(r, ms))
const list = await (await fetch(`http://127.0.0.1:${process.env.CDP_PORT || 9222}/json`)).json()
const target = list.find(p => p.type === 'page' && p.url.includes(process.env.PAGE || 'plair.live'))
if (!target) { console.log('no plair tab'); process.exit(1) }
const socket = new WebSocket(target.webSocketDebuggerUrl)
await new Promise(r => socket.addEventListener('open', r, { once: true }))
let id = 0
const pending = new Map()
const workers = []
socket.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Target.attachedToTarget') workers.push({ sessionId: m.params.sessionId, url: m.params.targetInfo.url, type: m.params.targetInfo.type })
})
const send = (method, params = {}, sessionId) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params, ...(sessionId ? { sessionId } : {}) })) })
await send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true })
await sleep(600)
const worker = workers.find(w => w.url.includes(wanted))
if (!worker) { console.log('no such worker running; workers:', workers.map(w => w.url.split('/').pop()).join(', ') || 'none'); process.exit(1) }
await send('Profiler.enable', {}, worker.sessionId)
await send('Profiler.setSamplingInterval', { interval: 250 }, worker.sessionId)
await send('Profiler.start', {}, worker.sessionId)
await sleep(seconds * 1000)
const { result } = await send('Profiler.stop', {}, worker.sessionId)
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
  return sources.get(file)?.[line]?.slice(column, column + 100).replace(/\s+/g, ' ') || ''
}
const label = node => `${node.callFrame.functionName || '(anonymous)'} ${(node.callFrame.url || '').split('/').pop()}:${node.callFrame.columnNumber}`
const selfBy = new Map()
const walk = (node, owner) => {
  const frame = node.callFrame
  const here = frame.url ? label(node) : owner
  const key = frame.url ? label(node) : `${frame.functionName}  <- ${owner}`
  if (node.self > 0) {
    const entry = selfBy.get(key) || { ms: 0, node }
    entry.ms += node.self
    selfBy.set(key, entry)
  }
  for (const child of node.children || []) walk(nodes.get(child), here)
}
walk(nodes.get(profile.nodes[0].id), '(top)')
const total = [...selfBy.values()].reduce((ms, entry) => ms + entry.ms, 0)
console.log(`${worker.url.split('/').pop()}: ${seconds} s profiled`)
for (const [key, entry] of [...selfBy].sort((a, b) => b[1].ms - a[1].ms).slice(0, 18)) {
  const frame = entry.node.callFrame
  console.log(`${(entry.ms / total * 100).toFixed(1).padStart(5)}%  ${(entry.ms / seconds).toFixed(0).padStart(4)} ms/s  ${key}  ${frame.url ? snippet(frame.url, frame.lineNumber, frame.columnNumber) : ''}`)
}
socket.close()
process.exit(0)
