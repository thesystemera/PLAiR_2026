// node fadecheck.mjs <url> [out dir] [width,height,scale] [mobile tab] — headless Chrome on the P6000: loads the app, stops the
// background scene on one frame, and screenshots that frame with the scrollers' edge fade done by the scene-fed strips
// and then by the CSS mask (`__plairEdgeFade.force`); covers are flat and the lights off so nothing else moves. perf/fillcheck_diff.py compares the pair. Also prints page errors.
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { killProfileChrome } from './killchrome.mjs'

const [url = 'http://127.0.0.1:4103', outDir = 'E:/scratch/plair/fadecheck', viewport = '1600,1000,1', panel = ''] = process.argv.slice(2)
const [width, height, scale] = viewport.split(',').map(Number)
const PORT = Number(process.env.PORT || 9338)
const sleep = ms => new Promise(r => setTimeout(r, ms))
mkdirSync(outDir, { recursive: true })
const profile = mkdtempSync(join(outDir, 'profile-'))
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--use-angle=d3d11', `--use-adapter-luid=${process.env.LUID || '0,9922884'}`,
  `--window-size=${width},${height}`, `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', 'about:blank',
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
socket.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Runtime.exceptionThrown') console.log('page exception:', JSON.stringify(m.params.exceptionDetails).slice(0, 400))
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') console.log('page error:', m.params.args.map(a => a.value ?? a.description ?? '').join(' ').slice(0, 300))
})
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
const evaluate = async expression => (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result?.result?.value
const shot = async name => {
  const { result } = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(join(outDir, `${name}.png`), Buffer.from(result.data, 'base64'))
}

let failed = null
try {
  await send('Runtime.enable')
  await send('Page.enable')
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: scale, mobile: width < 700 })
  await send('Page.navigate', { url })
  for (let i = 0; i < 80; i++) {
    if (await evaluate(`!!window.__plairEdgeFade && window.__plairEdgeFade.scrollers() > 0 && !document.getElementById('splash')`)) break
    await sleep(500)
  }
  await sleep(9000)
  console.log('fade mode:', await evaluate(`window.__plairEdgeFade.mode()`), '| scrollers:', await evaluate(`window.__plairEdgeFade.scrollers()`), '| scene thread:', await evaluate(`window.__plairScene?.thread?.()`))
  await evaluate(`window.__plairLight?.debug({ off: true }), window.__plairArt?.set({ lit: false }), 1`)
  if (process.env.EVAL) console.log('eval:', await evaluate(process.env.EVAL))
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
  await sleep(900)
  if (panel) {
    await evaluate(`(() => { const b = [...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim().startsWith(${JSON.stringify(panel)})); b?.click(); return 1 })()`)
    await sleep(1500)
  }
  await evaluate(`window.__plairEdgeFade.force('strips'), 1`)
  await sleep(800)
  await evaluate(`window.__plairScene.set({ skip: true }), 1`)
  await sleep(500)
  await shot('fade_new')
  if (process.env.STRIP_ROW) console.log('strip row:', await evaluate(`(() => { const wrap = document.querySelectorAll('[data-scroller]')[3].parentElement; const strip = [...wrap.querySelectorAll(':scope > canvas[aria-hidden]')][2]; const s = document.createElement('canvas'); s.width = strip.width; s.height = 1; const sc = s.getContext('2d', { willReadFrequently: true }); sc.drawImage(strip, 0, ${Number(process.env.STRIP_ROW)}, strip.width, 1, 0, 0, strip.width, 1); const raw = [...sc.getImageData(0, 0, strip.width, 1).data]; const box = strip.getBoundingClientRect(); return JSON.stringify({ at: [box.left, box.top], px: raw.reduce((rows, v, i) => { if (i % 4 === 0) rows.push([]); rows.at(-1).push(v); return rows }, []).map(p => p.join('/')) }) })()`))
  await evaluate(`window.__plairEdgeFade.force('mask'), 1`)
  await sleep(700)
  await shot('fade_old')
  await evaluate(`window.__plairScene.set({ skip: false }), window.__plairEdgeFade.force(null), 1`)
} catch (error) {
  failed = error
  console.log('FAILED:', error.message)
}
socket.close()
killProfileChrome(profile)
await sleep(500)
if (chrome.exitCode === null) await new Promise(r => { chrome.once('exit', r); setTimeout(r, 5000) })
try { rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 }) } catch (e) { console.error('profile cleanup failed', profile, e.message) }
process.exit(failed ? 1 : 0)
