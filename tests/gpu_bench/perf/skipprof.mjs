// node skipprof.mjs [min ms] [depth] [seconds] — CPU profile of one press of Next on the phone, printed as a call tree
// by inclusive time, with a snippet of the built file at each function so minified names can be read. Needs
// client/dist to be the build the phone runs. It changes the owner's song.
import { readFileSync, readdirSync } from 'node:fs'
import { page, sleep } from './cdp.mjs'

const minMs = Number(process.argv[2] || 8)
const maxDepth = Number(process.argv[3] || 9)
const seconds = Number(process.argv[4] || 1.6)
const DIST = 'E:/AI_RADIO/client/dist/assets/'
const sources = new Map()
const snippet = (url, line, column) => {
  const file = (url || '').split('/').pop().split('?')[0]
  if (!file) return ''
  if (!sources.has(file)) {
    let text = null
    try { if (readdirSync(DIST).includes(file)) text = readFileSync(DIST + file, 'utf8').split('\n') } catch { /* not built here */ }
    sources.set(file, text)
  }
  const text = sources.get(file)
  return text?.[line] ? text[line].slice(column, column + 90).replace(/\s+/g, ' ') : ''
}

const p = await page()
await p.send('Profiler.enable')
await p.send('Profiler.setSamplingInterval', { interval: 250 })
await p.send('Profiler.start')
await p.evaluate(`(() => { document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' })); return 1 })()`)
await sleep(seconds * 1000)
const { result } = await p.send('Profiler.stop')
const profile = result.profile
const nodes = new Map(profile.nodes.map(node => [node.id, { ...node, self: 0, total: 0 }]))
for (let i = 0; i < profile.samples.length; i++) nodes.get(profile.samples[i]).self += (profile.timeDeltas[i] || 0) / 1000
const sum = node => { node.total = node.self + (node.children || []).reduce((ms, id) => ms + sum(nodes.get(id)), 0); return node.total }
const root = profile.nodes[0]
sum(nodes.get(root.id))
const label = node => {
  const frame = node.callFrame
  const file = (frame.url || '').split('/').pop().split('?')[0]
  return `${frame.functionName || '(anonymous)'} ${file}:${frame.columnNumber}`
}
const print = (node, depth) => {
  if (node.total < minMs || depth > maxDepth) return
  const frame = node.callFrame
  console.log(`${'  '.repeat(depth)}${node.total.toFixed(0)} ms (self ${node.self.toFixed(0)})  ${label(node)}  ${snippet(frame.url, frame.lineNumber, frame.columnNumber)}`)
  for (const child of (node.children || []).map(id => nodes.get(id)).sort((a, b) => b.total - a.total)) print(child, depth + 1)
}
const isApp = node => /(^|\/)(index|sceneRenderer|AudioReactiveCanvas)-/.test(node.callFrame.url || '')
const entries = new Map()
const collect = (node, underApp, underReact) => {
  const app = isApp(node)
  const react = underReact || /vendor-react/.test(node.callFrame.url || '')
  if (app && !underApp) {
    const key = label(node)
    const entry = entries.get(key) || { ms: 0, calls: 0, react: 0, node }
    entry.ms += node.total
    entry.calls++
    if (react) entry.react += node.total
    entries.set(key, entry)
  }
  for (const id of node.children || []) collect(nodes.get(id), underApp || app, react)
}
if (process.env.SELF) {
  const selfBy = new Map()
  const walk = (node, owner) => {
    const frame = node.callFrame
    const here = isApp(node) || /vendor-/.test(frame.url || '') ? label(node) : owner
    const native = !frame.url
    const key = native ? `${frame.functionName}  <- ${owner}` : label(node)
    if (node.self > 0 && !/^\((idle|program|root)\)$/.test(frame.functionName)) {
      const entry = selfBy.get(key) || { ms: 0, node }
      entry.ms += node.self
      selfBy.set(key, entry)
    }
    for (const id of node.children || []) walk(nodes.get(id), here)
  }
  walk(nodes.get(root.id), '(top)')
  console.log('self time by function (native calls show their caller):')
  for (const [key, entry] of [...selfBy].sort((a, b) => b[1].ms - a[1].ms).slice(0, Number(process.env.SELF) || 40)) {
    const frame = entry.node.callFrame
    console.log(`${entry.ms.toFixed(0).padStart(5)} ms  ${key}  ${frame.url ? snippet(frame.url, frame.lineNumber, frame.columnNumber) : ''}`)
  }
  p.close()
  process.exit(0)
}
if (process.env.ENTRIES) {
  collect(nodes.get(root.id), false, false)
  const list = [...entries.values()].sort((a, b) => b.ms - a.ms).filter(entry => entry.ms >= minMs)
  console.log('app code entered from library or browser code, by inclusive ms (R = under React):')
  for (const entry of list) console.log(`${entry.ms.toFixed(0).padStart(5)} ms ${entry.react > entry.ms / 2 ? 'R' : ' '} ${label(entry.node)}  ${snippet(entry.node.callFrame.url, entry.node.callFrame.lineNumber, entry.node.callFrame.columnNumber)}`)
  p.close()
  process.exit(0)
}
const top = (nodes.get(root.id).children || []).map(id => nodes.get(id)).filter(node => !/^\((idle|program)\)$/.test(node.callFrame.functionName)).sort((a, b) => b.total - a.total)
console.log('busy ms:', top.reduce((ms, node) => ms + node.total, 0).toFixed(0))
for (const node of top) print(node, 0)
p.close()
process.exit(0)
