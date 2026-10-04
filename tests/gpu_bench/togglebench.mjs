// node togglebench.mjs [--url https://plair.live] [--cpu 4] [--panel queue] [--luid 0,86686]
// Desktop viewport on the P6000: closes then opens one panel, records every frame and traces the main thread.
import { spawn, execSync } from 'node:child_process'
import { killProfileChrome } from './killchrome.mjs'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, all) => arg.startsWith('--') ? [...pairs, [arg.slice(2), all[i + 1]]] : pairs, []))
const URL = args.url || 'https://plair.live'
const CPU = Number(args.cpu || 4)
const PANEL = args.panel || 'queue'
const PORT = 9337
const sleep = ms => new Promise(r => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), 'plair-toggle-'))
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--use-angle=d3d11', `--use-adapter-luid=${args.luid || '0,86686'}`,
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--window-size=1600,900', '--autoplay-policy=no-user-gesture-required', 'about:blank',
], { stdio: 'ignore' })
let ws
for (let i = 0; i < 50 && !ws; i++) {
  try { ws = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(p => p.type === 'page')?.webSocketDebuggerUrl } catch { /* starting */ }
  if (!ws) await sleep(200)
}
const socket = new WebSocket(ws)
await new Promise(r => socket.addEventListener('open', r, { once: true }))
let id = 0
const pending = new Map()
let events = []
let done = null
socket.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Tracing.dataCollected') events.push(...m.params.value)
  if (m.method === 'Tracing.tracingComplete') done?.()
})
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
const evaluate = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300))
  return r.result?.result?.value
}

await send('Page.enable')
await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1.5, mobile: false })
await send('Emulation.setCPUThrottlingRate', { rate: CPU })
await send('Page.navigate', { url: URL })
await sleep(4000)
await evaluate(`(() => {
  const saved = JSON.parse(localStorage.getItem('plair_settings') || '{}')
  localStorage.setItem('plair_settings', JSON.stringify({ ...saved, visualQuality: 'high', litArtwork: ${args.lit !== 'off'} }))
  localStorage.setItem('plair_quality_tier', '4')
  localStorage.setItem('plair_demo_mode_modal_seen', 'true')
})()`)
await send('Page.reload')
await sleep(10000)

const toggle = `(() => {
  const panel = document.querySelector('[data-shader-panel="${PANEL}"]')
  if (!panel) return 'no panel'
  const handle = panel.querySelector(':scope > div > div.absolute.top-0.right-0')
  if (handle) { handle.click(); return 'closed' }
  panel.click()
  return 'opened'
})()`

function summarizeProfile(profile) {
  const byId = new Map(profile.nodes.map(n => [n.id, n]))
  const parent = new Map()
  for (const n of profile.nodes) for (const c of n.children || []) parent.set(c, n.id)
  const selfUs = new Map()
  const totalUs = new Map()
  const key = f => `${f.functionName || '(anon)'} ${f.url.split('/').pop()}:${f.lineNumber + 1}:${f.columnNumber + 1}`
  profile.samples.forEach((sid, i) => {
    const dt = profile.timeDeltas[i + 1] ?? 0
    const k = key(byId.get(sid).callFrame)
    selfUs.set(k, (selfUs.get(k) || 0) + dt)
    const seen = new Set()
    for (let cur = sid; cur !== undefined; cur = parent.get(cur)) {
      const ck = key(byId.get(cur).callFrame)
      if (seen.has(ck)) continue
      seen.add(ck)
      totalUs.set(ck, (totalUs.get(ck) || 0) + dt)
    }
  })
  const fmt = (m, n) => [...m.entries()].filter(([k]) => !/^\((idle|program|root)\)/.test(k)).sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, us]) => `     ${(us / 1000).toFixed(1)} ms  ${k}`).join('\n')
  console.log(`   JS self:\n${fmt(selfUs, 14)}\n   JS total:\n${fmt(totalUs, 22)}`)
}

async function measure(label) {
  events = []
  if (args.profile) {
    await send('Profiler.enable')
    await send('Profiler.setSamplingInterval', { interval: 100 })
    await send('Profiler.start')
  }
  await send('Tracing.start', { categories: 'toplevel,blink,devtools.timeline,disabled-by-default-devtools.timeline,v8.execute', transferMode: 'ReportEvents' })
  if (args.light) await evaluate(`window.__plairLight?.debug({ level: ${Number(args.light)} })`)
  const result = await evaluate(`new Promise(resolve => {
    const times = []
    let last = performance.now()
    const start = last
    let action = null
    const step = (now) => {
      times.push(+(now - last).toFixed(1))
      last = now
      if (!action && now - start > 300) action = ${toggle}
      if (now - start < 1800) requestAnimationFrame(step)
      else resolve({ action, frames: times.length, over20: times.filter(t => t > 20).length, over34: times.filter(t => t > 34).length, max: Math.max(...times), times: times.slice(0, 80).join(' ') })
    }
    requestAnimationFrame(step)
  })`)
  const profileResult = args.profile ? (await send('Profiler.stop')).result.profile : null
  const complete = new Promise(r => { done = r })
  await send('Tracing.end')
  await complete
  const names = new Map(events.filter(e => e.ph === 'M' && e.name === 'thread_name').map(e => [`${e.pid}:${e.tid}`, e.args.name]))
  const main = events.filter(e => e.ph === 'X' && /CrRendererMain/.test(names.get(`${e.pid}:${e.tid}`) || ''))
  const totals = {}
  for (const e of main) if (/^(Layout|UpdateLayoutTree|RecalcStyle|Paint|PrePaint|FunctionCall|EventDispatch|FireAnimationFrame|UpdateLayerTree|Commit|ParseHTML|IntersectionObserverController::computeIntersections|Blink.PrePaint.UpdateTime|LayerTreeHost::DoUpdateLayers|ScheduleStyleRecalculation|Document::recalcStyle|LocalFrameView::layout)$/.test(e.name)) totals[e.name] = (totals[e.name] || 0) + e.dur / 1000
  const layouts = main.filter(e => e.name === 'Layout')
  const longest = main.filter(e => e.dur > 16000).sort((a, b) => b.dur - a.dur).slice(0, 6).map(e => `${e.name} ${(e.dur / 1000).toFixed(1)}ms${e.args?.data?.functionName ? ' ' + e.args.data.functionName : ''}${e.args?.beginData?.stackTrace ? ' forced' : ''}`)
  console.log(`--- ${label}: ${result.action}, ${result.frames} frames in 1.8 s, >20ms: ${result.over20}, >34ms: ${result.over34}, max ${result.max} ms`)
  console.log('   frame ms:', result.times)
  console.log('   main thread ms:', Object.entries(totals).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v.toFixed(1)}`).join(' | '))
  console.log(`   layouts: ${layouts.length}, total ${layouts.reduce((n, e) => n + e.dur, 0) / 1000} ms; longest tasks: ${longest.join(' ; ')}`)
  if (profileResult) summarizeProfile(profileResult)
}

console.log('tiles:', await evaluate(`document.querySelectorAll('canvas[aria-hidden="true"]').length`), 'light:', await evaluate(`JSON.stringify(window.__plairLight?.read().level)`))
await measure('close')
await sleep(1500)
await measure('open')
socket.close(); killProfileChrome(profile); await sleep(500)
if (chrome.exitCode === null) await new Promise(r => { chrome.once("exit", r); setTimeout(r, 5000) }); try { rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 }) } catch (e) { console.error("profile cleanup failed", profile, e.message) }
