// node pagerun.mjs <url> <seconds> <expression>  — open a page in headless Chrome on the P6000, trace main-thread GPU waits, print an expression
import { spawn, execSync } from 'node:child_process'
import { killProfileChrome } from './killchrome.mjs'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const [url, seconds = '5', expression = 'window.results'] = process.argv.slice(2)
const PORT = 9336
const sleep = ms => new Promise(r => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), 'plair-page-'))
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', ...(process.env.SWIFTSHADER ? ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] : ['--use-angle=d3d11', `--use-adapter-luid=${process.env.LUID || '0,86686'}`]),
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', 'about:blank',
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
const events = []
let done = null
socket.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Tracing.dataCollected') events.push(...m.params.value)
  if (m.method === 'Tracing.tracingComplete') done?.()
  if (m.method === 'Runtime.exceptionThrown') console.log('page exception:', JSON.stringify(m.params.exceptionDetails).slice(0, 600))
  if (m.method === 'Log.entryAdded') console.log('page log:', m.params.entry.level, m.params.entry.text, m.params.entry.url || '')
})
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
await send('Runtime.enable')
await send('Log.enable')
if (process.env.PRELOAD) { await send('Page.enable'); await send('Page.addScriptToEvaluateOnNewDocument', { source: process.env.PRELOAD }) }
await send('Page.navigate', { url })
await sleep(2000)
await send('Tracing.start', { categories: 'toplevel,gpu,disabled-by-default-gpu.service', transferMode: 'ReportEvents' })
await sleep(Number(seconds) * 1000)
const complete = new Promise(r => { done = r })
await send('Tracing.end')
await complete
const names = new Map(events.filter(e => e.ph === 'M' && e.name === 'thread_name').map(e => [`${e.pid}:${e.tid}`, e.args.name]))
const waits = events.filter(e => e.ph === 'X' && /WaitForGetOffset|CommandBufferHelper::Finish/.test(e.name) && /CrRendererMain/.test(names.get(`${e.pid}:${e.tid}`) || ''))
console.log(`main-thread GPU waits: ${waits.length} in ${seconds}s, total ${(waits.reduce((n, e) => n + e.dur, 0) / 1000).toFixed(1)} ms`)
const r = await send('Runtime.evaluate', { expression: `JSON.stringify(${expression})`, returnByValue: true })
console.log(r.result?.result?.value)
socket.close(); killProfileChrome(profile); await sleep(500)
if (chrome.exitCode === null) await new Promise(r => { chrome.once("exit", r); setTimeout(r, 5000) }); try { rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 }) } catch (e) { console.error("profile cleanup failed", profile, e.message) }
