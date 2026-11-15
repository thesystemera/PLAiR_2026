// node scenelab.mjs <url> <out dir> '<expression>' ... : opens client/scene-lab.html (Vite dev server) in headless Chrome
// on the P6000, waits for window.lab, awaits each expression in turn and prints its result. Any `image` data URL in a
// result is saved as <out dir>/<n>.png. LUID=high,low picks the card (python list_gpus.py); SWIFTSHADER=1 uses software.
import { spawn } from 'node:child_process'
import { killProfileChrome } from './killchrome.mjs'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const [url, outDir, ...expressions] = process.argv.slice(2)
const PORT = 9341
const sleep = ms => new Promise(r => setTimeout(r, ms))
mkdirSync(outDir, { recursive: true })
const profile = mkdtempSync(join(tmpdir(), 'plair-lab-'))
const gpu = process.env.SWIFTSHADER
  ? ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']
  : ['--use-angle=d3d11', `--use-adapter-luid=${process.env.LUID || '0,9922884'}`]
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', ...gpu, `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  `--window-size=${process.env.WINDOW || '1400,1100'}`, '--no-first-run', '--no-default-browser-check', 'about:blank',
], { stdio: 'ignore' })

let socket
try {
  let ws
  for (let i = 0; i < 50 && !ws; i++) {
    try { ws = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(p => p.type === 'page')?.webSocketDebuggerUrl } catch { /* starting */ }
    if (!ws) await sleep(200)
  }
  socket = new WebSocket(ws)
  await new Promise(r => socket.addEventListener('open', r, { once: true }))
  let id = 0
  const pending = new Map()
  socket.addEventListener('message', e => {
    const m = JSON.parse(e.data)
    if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
    if (m.method === 'Runtime.exceptionThrown') console.log('page exception:', JSON.stringify(m.params.exceptionDetails).slice(0, 900))
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') console.log('page error:', m.params.args.map(a => a.value ?? a.description).join(' ').slice(0, 900))
  })
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
  const evaluate = async expression => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 900))
    return r.result?.result?.value
  }
  await send('Runtime.enable')
  await send('Page.navigate', { url })
  for (let i = 0; i < 240; i++) {
    if (await evaluate('!!window.lab?.ready').catch(() => false)) break
    await sleep(250)
  }
  const card = await evaluate(`(() => { const gl = window.lab.scene.gl; const x = gl.getExtension('WEBGL_debug_renderer_info'); return gl.getParameter(x ? x.UNMASKED_RENDERER_WEBGL : gl.RENDERER) })()`)
  console.log('renderer:', card)
  if (!process.env.SWIFTSHADER && !/P6000/.test(card)) throw new Error('not on the P6000, stopping')
  console.log(await evaluate('document.getElementById("info").textContent'))
  let n = 0
  for (const expression of expressions) {
    const value = await evaluate(`(async () => (${expression}))()`)
    if (value?.image) {
      const file = join(outDir, `${++n}.png`)
      writeFileSync(file, Buffer.from(value.image.split(',')[1], 'base64'))
      value.image = file
    }
    console.log(JSON.stringify(value, null, process.env.PRETTY ? 1 : 0))
  }
} catch (error) {
  console.log('failed:', error.message)
} finally {
  socket?.close()
  killProfileChrome(profile)
  await sleep(500)
  if (chrome.exitCode === null) await new Promise(r => { chrome.once('exit', r); setTimeout(r, 5000) })
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 }) } catch (e) { console.error('profile cleanup failed', profile, e.message) }
}
