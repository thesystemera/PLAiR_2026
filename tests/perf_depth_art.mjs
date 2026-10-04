// Frame-time benchmark for the depth/light artwork on a simulated low-end phone.
//   node tests/perf_depth_art.mjs [--url https://plair.live] [--cpu 4] [--seconds 8] [--shots dir]
// Chrome runs headless with SwiftShader (software GPU: slow like a budget phone, and no graphics card is used),
// a 412x915 phone screen at 2.625x, an Android user agent and the CPU slowed down. Tilt is simulated with
// deviceorientation events and the light is held fully on, so every frame does the full work.
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, all) => arg.startsWith('--') ? [...pairs, [arg.slice(2), all[i + 1]]] : pairs, []))
const URL = args.url || 'https://plair.live'
const CPU = Number(args.cpu || 4)
const SECONDS = Number(args.seconds || 8)
const PORT = 9333
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const UA = 'Mozilla/5.0 (Linux; Android 12; SM-A135F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36'

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const profile = mkdtempSync(join(tmpdir(), 'plair-perf-'))
const chrome = spawn(CHROME, [
  '--headless=new', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--window-size=412,915', '--autoplay-policy=no-user-gesture-required', 'about:blank',
], { stdio: 'ignore' })

async function connect() {
  for (let i = 0; i < 50; i++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      const page = pages.find(p => p.type === 'page')
      if (page) return page.webSocketDebuggerUrl
    } catch { /* chrome still starting */ }
    await sleep(200)
  }
  throw new Error('Chrome did not start')
}

const socket = new WebSocket(await connect())
await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }))
let nextId = 0
const pending = new Map()
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data)
  if (message.id && pending.has(message.id)) {
    pending.get(message.id)(message)
    pending.delete(message.id)
  }
})
const send = (method, params = {}) => new Promise(resolve => {
  const id = ++nextId
  pending.set(id, resolve)
  socket.send(JSON.stringify({ id, method, params }))
})
const evaluate = async (expression) => {
  const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (result.result?.exceptionDetails) throw new Error(JSON.stringify(result.result.exceptionDetails))
  return result.result?.result?.value
}

await send('Page.enable')
await send('Runtime.enable')
await send('Emulation.setDeviceMetricsOverride', { width: 412, height: 915, deviceScaleFactor: 2.625, mobile: true })
await send('Emulation.setUserAgentOverride', { userAgent: UA, platform: 'Android' })
await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 })
await send('Emulation.setCPUThrottlingRate', { rate: CPU })

async function load(litArtwork) {
  await send('Page.navigate', { url: URL })
  await sleep(4000)
  await evaluate(`(() => {
    const saved = JSON.parse(localStorage.getItem('plair_settings') || '{}')
    localStorage.setItem('plair_settings', JSON.stringify({ ...saved, litArtwork: ${litArtwork}, visualQuality: 'high' }))
    localStorage.setItem('plair_quality_tier', '4')
    localStorage.setItem('plair_demo_mode_modal_seen', 'true')
  })()`)
  await send('Page.reload')
  await sleep(9000)
}

async function openTab(label) {
  await evaluate(`(() => {
    const button = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(label)})
    if (button) button.click()
  })()`)
  await sleep(2500)
}

async function measure(tilt = true, level = 1) {
  return evaluate(`new Promise(resolve => {
    window.__plairLight?.debug({ level: ${level} })
    window.__plairProfile = {}
    const times = []
    const start = performance.now()
    let last = start
    const step = (now) => {
      const t = (now - start) / 1000
      if (${tilt}) window.dispatchEvent(Object.assign(new Event('deviceorientation'), { alpha: 0, beta: 20 * Math.sin(t * 1.3), gamma: 20 * Math.cos(t * 0.9) }))
      times.push(now - last)
      last = now
      if (now - start < ${SECONDS * 1000}) requestAnimationFrame(step)
      else {
        window.__plairLight?.debug({ level: null })
        const sorted = times.slice(5).sort((a, b) => a - b)
        const pick = q => +sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))].toFixed(1)
        const drawnTiles = [...document.querySelectorAll('canvas[aria-hidden="true"]')].filter(c => c.style.opacity === '1').length
        const profile = window.__plairProfile
        window.__plairProfile = null
        const frames = sorted.length || 1
        const tiles = profile.tiles || { frames: 0, drawMs: 0, copyMs: 0, pixels: 0 }
        const np = profile.nowPlaying || { frames: 0, drawMs: 0, pixels: 0 }
        resolve({
          fps: +(frames / ((now - start) / 1000)).toFixed(1), p50ms: pick(0.5), p95ms: pick(0.95), drawnTiles,
          tileDrawMs: +(tiles.drawMs / frames).toFixed(1), tileCopyMs: +(tiles.copyMs / frames).toFixed(1),
          tileMpx: +(tiles.pixels / frames / 1e6).toFixed(2),
          npDrawMs: +(np.drawMs / frames).toFixed(1), npMpx: +(np.pixels / frames / 1e6).toFixed(2),
        })
      }
    }
    requestAnimationFrame(step)
  })`)
}

const results = []
for (const lit of [true, false]) {
  await load(lit)
  for (const tab of ['Catalog', 'Playing']) {
    await openTab(tab)
    const modes = lit && args.isolate ? [['tilt+light', true, 1], ['tilt only', true, 0], ['light only', false, 1], ['still', false, 0]] : [['tilt+light', true, 1]]
    for (const [mode, tilt, level] of modes) results.push({ litArtwork: lit, tab, mode, ...(await measure(tilt, level)) })
    if (args.shots) {
      mkdirSync(args.shots, { recursive: true })
      const shot = await send('Page.captureScreenshot', { format: 'png' })
      writeFileSync(join(args.shots, `${tab}-${lit ? 'lit' : 'flat'}.png`), Buffer.from(shot.result.data, 'base64'))
    }
  }
}
console.table(results)

socket.close()
chrome.kill()
await sleep(500)
try { rmSync(profile, { recursive: true, force: true }) } catch { /* chrome may still hold files */ }
