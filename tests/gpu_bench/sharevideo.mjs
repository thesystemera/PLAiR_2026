// node sharevideo.mjs <url> [close|cancel|stay]  — render a share video for the song on air in headless Chrome on the
// P6000 (LUID=high,low from list_gpus.py) and report how long it took. `close` shuts the Share window while it renders
// and waits for the "Video ready" notice; `cancel` cancels after 3 s; `stay` (default) keeps the window open.
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { killProfileChrome } from './killchrome.mjs'

const [url = 'http://127.0.0.1:4103', mode = 'stay'] = process.argv.slice(2)
const PORT = Number(process.env.PORT || 9337)
const SCRATCH = process.env.SCRATCH || 'E:/scratch/plair/sharevideo'
const TIMEOUT_S = Number(process.env.TIMEOUT_S || 600)
const sleep = ms => new Promise(r => setTimeout(r, ms))

mkdirSync(SCRATCH, { recursive: true })
const profile = mkdtempSync(join(SCRATCH, 'profile-'))
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--use-angle=d3d11', `--use-adapter-luid=${process.env.LUID || '0,9922884'}`,
  '--window-size=1600,1000', '--autoplay-policy=no-user-gesture-required',
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
const lines = []
socket.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  if (m.method === 'Runtime.consoleAPICalled') {
    const text = m.params.args.map(a => a.value ?? (a.preview ? JSON.stringify(Object.fromEntries(a.preview.properties.map(p => [p.name, p.value]))) : a.description) ?? '').join(' ')
    if (/ShareVideo|OfflineVideoRenderer|ShareModal|\[ERROR\]|\[CRITICAL\]/.test(text)) { lines.push(text); console.log('  page:', text.slice(0, 400)) }
  }
  if (m.method === 'Runtime.exceptionThrown') console.log('  page exception:', JSON.stringify(m.params.exceptionDetails).slice(0, 500))
})
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
const evaluate = async expression => (await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result?.result?.value
const until = async (expression, seconds, label) => {
  const end = Date.now() + seconds * 1000
  while (Date.now() < end) {
    const value = await evaluate(expression)
    if (value) return value
    await sleep(250)
  }
  throw new Error(`timed out waiting for ${label}`)
}
const clickText = text => evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(${JSON.stringify(text)})); if (!b || b.disabled) return false; b.click(); return true })()`)
const shot = async name => {
  const { result } = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(join(SCRATCH, `${name}.png`), Buffer.from(result.data, 'base64'))
}

let failed = null
try {
  await send('Runtime.enable')
  await send('Page.enable')
  await send('Page.navigate', { url })
  const renderer = await until(`(() => { const c = document.createElement('canvas').getContext('webgl2'); const d = c?.getExtension('WEBGL_debug_renderer_info'); return d ? c.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'unknown' })()`, 10, 'WebGL')
  console.log('GPU:', renderer)
  if (!/P6000/.test(renderer) && !process.env.ANY_GPU) throw new Error('not on the P6000; pass LUID=high,low from list_gpus.py')

  await until(`(() => { const b = document.querySelector('button[title="Share track"]'); return !!b && !b.disabled })()`, 40, 'a song on air')
  await sleep(1500)
  await evaluate(`document.querySelector('button[title="Share track"]').click()`)
  await until(`[...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('Generate Video'))`, 10, 'the Share window')
  await sleep(600)
  const started = Date.now()
  await clickText('Generate Video')
  console.log('render started')

  const ready = `!!document.querySelector('video[src^="blob:"]')`
  const failedText = `document.body.innerText.includes('Generation Failed')`
  const noticeReady = `document.body.innerText.includes('Video ready')`

  if (mode === 'cancel') {
    await sleep(3000)
    await shot('rendering')
    console.log('cancel clicked:', await clickText('Cancel'))
    await until(`[...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('Generate Video'))`, 10, 'the idle Share window after cancel')
    console.log('cancelled; Share window is back to idle')
  } else {
    if (mode === 'close') {
      await sleep(3000)
      await shot('rendering')
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
      await sleep(1000)
      console.log('Share window closed:', await evaluate(`![...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Cancel')`))
      await shot('closed-while-rendering')
    }
    let last = ''
    const end = Date.now() + TIMEOUT_S * 1000
    for (;;) {
      if (await evaluate(mode === 'close' ? noticeReady : ready)) break
      if (await evaluate(failedText)) throw new Error('the page says Generation Failed')
      if (Date.now() > end) throw new Error('render timed out')
      const percent = await evaluate(`(document.body.innerText.match(/(?:^|\\n)(\\d{1,3})%(?:\\n|$)/) || [])[1] || ''`)
      if (percent && percent !== last && Number(percent) % 20 === 0) { last = percent; console.log(`  ${percent}% at ${((Date.now() - started) / 1000).toFixed(1)} s`) }
      await sleep(500)
    }
    const seconds = (Date.now() - started) / 1000
    console.log(`video ready after ${seconds.toFixed(1)} s`)
    await shot('ready')
    if (mode === 'close') {
      console.log('Open clicked:', await clickText('Open'))
      await until(ready, 10, 'the finished video in the Share window')
      await sleep(800)
      await shot('reopened')
    }
    const info = await evaluate(`(async () => { const v = document.querySelector('video[src^="blob:"]'); if (v.readyState < 1) await new Promise(r => v.addEventListener('loadedmetadata', r, { once: true })); const size = (document.body.innerText.match(/MP4 ready - ([\\d.]+) MB/) || [])[1]; return JSON.stringify({ duration: v.duration, width: v.videoWidth, height: v.videoHeight, sizeMB: size }) })()`)
    console.log('video:', info)
  }
} catch (error) {
  failed = error
  console.log('FAILED:', error.message)
  try { await shot('failed') } catch { /* no page */ }
}

socket.close()
killProfileChrome(profile)
await sleep(500)
if (chrome.exitCode === null) await new Promise(r => { chrome.once('exit', r); setTimeout(r, 5000) })
try { rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 }) } catch (e) { console.error('profile cleanup failed', profile, e.message) }
process.exit(failed ? 1 : 0)
