// node vizframe.mjs [panel] — one compositor frame from the phone: every render pass Chrome draws for the page (each
// one beyond the last is an off-screen texture), its rectangle, what changed inside it and how many quads it holds,
// then the layers with their sizes and memory. `surfaces.mjs` names the CSS that forces the passes. Changes nothing.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2]
const p = await page()
if (panel) {
  await openPanel(p, panel)
  await sleep(2000)
}
const version = await (await fetch('http://127.0.0.1:9222/json/version')).json()
const socket = new WebSocket(version.webSocketDebuggerUrl)
await new Promise(r => socket.addEventListener('open', r, { once: true }))
let id = 0
const pending = new Map()
const events = []
let done
socket.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Tracing.dataCollected') events.push(...m.params.value)
  if (m.method === 'Tracing.tracingComplete') done?.()
})
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
await send('Tracing.start', { categories: 'viz,disabled-by-default-viz.quads', transferMode: 'ReportEvents' })
await sleep(900)
const complete = new Promise(r => { done = r })
await send('Tracing.end')
await complete
socket.close()
p.close()

const frames = events.filter(e => e.name === 'DirectRenderer::DrawFrame').length
const drawn = events.filter(e => e.name === 'DirectRenderer::DrawRenderPass').length
console.log(`${frames} frames, ${(drawn / Math.max(1, frames)).toFixed(2)} render passes drawn per frame`)
const snapshot = events.filter(e => e.name === 'LayerTreeHostImpl:snapshot' && e.args?.snapshot?.frame).at(-1)?.args.snapshot
if (!snapshot) { console.log('no layer tree snapshot in the trace'); process.exit(1) }
for (const pass of snapshot.frame.render_passes) {
  const kinds = {}
  for (const quad of pass.quad_list || []) kinds[quad.material] = (kinds[quad.material] || 0) + 1
  console.log(`pass ${JSON.stringify(pass.output_rect)} changed ${JSON.stringify(pass.damage_rect)} quads ${(pass.quad_list || []).length} ${JSON.stringify(kinds)}`)
}
for (const layer of snapshot.active_tree.layers) {
  if (!layer.draws_content || !layer.layer_name) continue
  console.log(`layer ${String(layer.bounds.width).padStart(5)}x${String(layer.bounds.height).padEnd(6)} ${(layer.gpu_memory_usage / 1048576).toFixed(1).padStart(5)} MB  ${layer.layer_name.slice(0, 110)}`)
}
process.exit(0)
