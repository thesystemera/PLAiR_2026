// node phone.mjs eval '<expr>' | fps <seconds> | trace <seconds>  — measure the PLAiR tab on the phone over USB
// needs: adb forward tcp:9222 localabstract:chrome_devtools_remote
setTimeout(() => { console.error('phone.mjs timed out (tab hidden?)'); process.exit(2) }, 60000).unref()
const [cmd = 'fps', arg = '5', arg2] = process.argv.slice(2)
const sleep = ms => new Promise(r => setTimeout(r, ms))
const list = await (await fetch('http://127.0.0.1:9222/json')).json()
const page = list.find(p => p.type === 'page' && p.url.includes('plair.live'))
if (!page) { console.error('no plair.live tab'); process.exit(1) }

function connect(url) {
  const socket = new WebSocket(url)
  let id = 0
  const pending = new Map()
  const listeners = []
  socket.addEventListener('message', e => {
    const m = JSON.parse(e.data)
    if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
    for (const l of listeners) l(m)
  })
  const ready = new Promise(r => socket.addEventListener('open', r, { once: true }))
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
  return { ready, send, on: f => listeners.push(f), close: () => socket.close() }
}

const p = connect(page.webSocketDebuggerUrl)
await p.ready
const evaluate = async (expression, awaitPromise = true) => {
  const r = await p.send('Runtime.evaluate', { expression, awaitPromise, returnByValue: true })
  if (r.result?.exceptionDetails) return 'ERR ' + JSON.stringify(r.result.exceptionDetails).slice(0, 400)
  return r.result?.result?.value
}

const FPS_EXPR = seconds => `new Promise(resolve => {
  const d = []; let last = 0; const end = performance.now() + ${seconds} * 1000
  const tick = t => { if (last) d.push(t - last); last = t; if (t < end) requestAnimationFrame(tick); else done() }
  const done = () => {
    const s = [...d].sort((a, b) => a - b); const q = f => +s[Math.min(s.length - 1, Math.floor(s.length * f))].toFixed(2)
    const total = d.reduce((a, b) => a + b, 0)
    resolve(JSON.stringify({ fps: +(d.length / total * 1000).toFixed(1), p50: q(0.5), p90: q(0.9), p99: q(0.99), over12: d.filter(x => x > 12).length, over20: d.filter(x => x > 20).length, frames: d.length, visible: document.visibilityState, quality: window.__plairQuality?.() }))
  }
  requestAnimationFrame(tick)
})`

const ADB = process.env.ADB || 'adb'
async function gpuBusy(seconds) {
  const { execFileSync } = await import('node:child_process')
  const samples = []
  const end = Date.now() + seconds * 1000
  let last = ''
  while (Date.now() < end) {
    const raw = execFileSync(ADB, ['shell', 'cat /sys/class/kgsl/kgsl-3d0/gpubusy']).toString().trim()
    if (raw !== last) { last = raw; const [b, t] = raw.split(/\s+/).map(Number); if (t > 0) samples.push(b / t) }
    await sleep(250)
  }
  samples.shift()
  return samples.length ? +(100 * samples.reduce((a, b) => a + b, 0) / samples.length).toFixed(1) : null
}

if (cmd === 'busy') {
  const [fps, busy] = await Promise.all([evaluate(FPS_EXPR(Number(arg))), gpuBusy(Number(arg))])
  const f = JSON.parse(fps)
  console.log(`${(arg2 || '').padEnd(14)} gpu busy ${busy}%  fps ${f.fps}  p50 ${f.p50}  p90 ${f.p90}  p99 ${f.p99}  >20ms ${f.over20}  ${f.quality?.level}`)
} else if (cmd === 'css') {
  console.log(await evaluate(`(() => { let s = document.getElementById('__bench_css'); if (!s) { s = document.createElement('style'); s.id = '__bench_css'; document.head.appendChild(s) } s.textContent = ${JSON.stringify(arg)}; return 'css set' })()`))
} else if (cmd === 'reload') {
  await p.send('Page.reload', { ignoreCache: true })
  await sleep(8000)
  console.log(await evaluate('JSON.stringify(window.__plairQuality?.())'))
} else if (cmd === 'tap') {
  const [x, y] = arg.split(',').map(Number)
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] })
  await sleep(60)
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  console.log('tapped', x, y)
} else if (cmd === 'layers') {
  let layers = null
  p.on(m => { if (m.method === 'LayerTree.layerTreeDidChange' && m.params.layers) layers = m.params.layers })
  await p.send('LayerTree.enable')
  await sleep(1500)
  await p.send('LayerTree.disable')
  const vw = Number(arg2 || 375), vh = 728
  const rows = (layers || []).filter(l => l.drawsContent && !l.invisible).map(l => ({ id: l.layerId, node: l.backendNodeId, w: Math.round(l.width), h: Math.round(l.height), x: Math.round(l.offsetX), y: Math.round(l.offsetY), area: +(l.width * l.height / (vw * vh)).toFixed(2), paints: l.paintCount }))
  rows.sort((a, b) => b.area - a.area)
  const total = rows.reduce((n, r) => n + r.area, 0)
  console.log(`${rows.length} drawing layers, total area ${total.toFixed(2)} screens`)
  await p.send('DOM.getDocument', { depth: 0 })
  for (const r of rows.slice(0, Number(arg) || 25)) {
    let desc = ''
    try { const d = await p.send('DOM.describeNode', { backendNodeId: r.node }); const n = d.result.node; desc = `${n.localName}${(n.attributes || []).reduce((s, v, i, a) => i % 2 === 0 && v === 'class' ? s + '.' + a[i + 1].slice(0, 90) : s, '')}` } catch {}
    console.log(`${String(r.area).padStart(5)}  ${r.w}x${r.h}@${r.x},${r.y} paints ${r.paints}  ${desc}`)
  }
} else if (cmd === 'gpu') {
  await evaluate(`__plairScene.set({ force: true, timing: { pixel: new Uint8Array(4), repeat: ${Number(process.env.REPEAT || 1)} } }); 1`)
  await sleep(Number(arg) * 1000)
  const t = JSON.parse(await evaluate(`__plairScene.eval('const t = scene.bench.timing; scene.bench.timing = null; scene.bench.force = false; return JSON.stringify(t)')`))
  console.log(`${(arg2 || '').padEnd(16)} main ${(t.main / t.frames).toFixed(2).padStart(6)} ms   capture ${t.captures ? (t.capture / t.captures).toFixed(2) : 0} ms (${t.captures || 0}/${t.frames} frames)`)
} else if (cmd === 'ft') {
  const f = JSON.parse(await evaluate(FPS_EXPR(Number(arg))))
  console.log(`${(arg2 || '').padEnd(18)} frame ${(1000 / f.fps).toFixed(2)} ms  fps ${f.fps}  p50 ${f.p50}  p90 ${f.p90}`)
} else if (cmd === 'eval') {
  console.log(await evaluate(arg))
} else if (cmd === 'fps') {
  console.log(await evaluate(FPS_EXPR(Number(arg))))
} else if (cmd === 'trace') {
  const version = await (await fetch('http://127.0.0.1:9222/json/version')).json()
  const b = connect(version.webSocketDebuggerUrl)
  await b.ready
  const events = []
  let done
  b.on(m => {
    if (m.method === 'Tracing.dataCollected') events.push(...m.params.value)
    if (m.method === 'Tracing.tracingComplete') done?.()
  })
  await b.send('Tracing.start', { categories: 'toplevel,viz,gpu,cc,benchmark,disabled-by-default-devtools.timeline', transferMode: 'ReportEvents' })
  const fps = evaluate(FPS_EXPR(Number(arg)))
  await sleep(Number(arg) * 1000 + 300)
  const complete = new Promise(r => { done = r })
  await b.send('Tracing.end')
  await complete
  if (process.env.PASSES) {
    const frames = events.filter(e => e.name === 'DirectRenderer::DrawFrame').length
    const passes = events.filter(e => e.name === 'DirectRenderer::DrawRenderPass')
    const quads = passes.reduce((n, e) => n + (e.args?.NumberOfQuads || 0), 0)
    const ids = new Map(); for (const e of passes) ids.set(e.args?.id, (ids.get(e.args?.id) || 0) + 1)
    const f = JSON.parse(await fps)
    console.log(`${(process.env.PASSES).padEnd(14)} passes/frame ${(passes.length / frames).toFixed(2)}  quads/frame ${(quads / frames).toFixed(1)}  fps ${f.fps}  passes seen ${[...ids].filter(([, n]) => n > frames * 0.3).map(([id]) => id).join(',')}`)
    b.close(); p.close(); process.exit(0)
  }
  console.log(await fps)
  const names = new Map(events.filter(e => e.ph === 'M' && e.name === 'thread_name').map(e => [`${e.pid}:${e.tid}`, e.args.name]))
  const busy = new Map()
  const tops = new Map()
  for (const e of events) {
    if (e.ph !== 'X' || !e.dur) continue
    const thread = names.get(`${e.pid}:${e.tid}`) || `${e.pid}:${e.tid}`
    if (e.name === 'ThreadControllerImpl::RunTask' || e.name === 'ThreadPool_RunTask' || e.name === 'Scheduler::RunNextTask' || e.name === 'TaskGraphRunner::RunTask') busy.set(thread, (busy.get(thread) || 0) + e.dur)
    const key = `${thread} | ${e.name}`
    tops.set(key, (tops.get(key) || 0) + e.dur)
  }
  const secs = Number(arg)
  console.log('thread busy ms/s:', [...busy].sort((a, b) => b[1] - a[1]).slice(0, 10).map(([k, v]) => `${k}=${(v / 1000 / secs).toFixed(0)}`).join('  '))
  const n = Number(arg2 || 25)
  for (const [k, v] of [...tops].sort((a, b) => b[1] - a[1]).slice(0, n)) console.log(`${(v / 1000 / secs).toFixed(1).padStart(7)} ms/s  ${k}`)
  if (process.env.SAVE) (await import('node:fs')).writeFileSync(process.env.SAVE, JSON.stringify(events))
  b.close()
}
p.close()
