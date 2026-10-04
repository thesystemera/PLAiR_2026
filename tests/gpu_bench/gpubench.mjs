// GPU benchmark: node gpubench.mjs --urls http://127.0.0.1:4101,http://127.0.0.1:4102 [--luid 0,86686 --gpu P6000] [--uncapped 1] [--cpu 4] [--seconds 6] [--tabs Catalog,Playing] [--lit on,off] [--modes tilt+light,still]
import { spawn, execSync } from 'node:child_process'
import { killProfileChrome } from './killchrome.mjs'
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, all) => arg.startsWith('--') ? [...pairs, [arg.slice(2), all[i + 1]]] : pairs, []))
const URLS = (args.urls || 'https://plair.live').split(',')
const CPU = Number(args.cpu || 4)
const SECONDS = Number(args.seconds || 6)
const TABS = (args.tabs || 'Catalog,Playing').split(',')
const LIT = (args.lit || 'on').split(',').map(v => v === 'on')
const MODES = (args.modes || 'tilt+light').split(',')
const REPEAT = Number(args.repeat || 1)
const PORT = 9335
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const UA = 'Mozilla/5.0 (Linux; Android 14; 23129RAA4G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36'
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

const gpuFlags = args.luid
  ? ['--use-angle=d3d11', `--use-adapter-luid=${args.luid}`]
  : ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']
const pacing = args.uncapped ? ['--disable-gpu-vsync', '--disable-frame-rate-limit'] : []

const INSTRUMENT = `(() => {
  const stats = window.__gpuStats = { ms: {}, draws: {}, frames: 0, on: false, reset() { this.ms = {}; this.draws = {}; this.frames = 0 } }
  const states = new WeakMap()
  const pending = []
  const setup = (gl) => {
    const gl2 = typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext
    const ext = gl.getExtension(gl2 ? 'EXT_disjoint_timer_query_webgl2' : 'EXT_disjoint_timer_query')
    const canvas = gl.canvas
    const kind = !canvas.isConnected ? 'tiles' : (canvas.parentElement && canvas.closest('[data-engine]')) || canvas.dataset.engine ? 'scene' : 'nowPlaying'
    return { gl, gl2, ext, kind, vp: [0, 0, 0, 0], fb: null }
  }
  const state = (gl) => { let s = states.get(gl); if (!s) { s = setup(gl); states.set(gl, s) } return s }
  const label = (s) => {
    if (s.kind !== 'scene') return s.kind
    if (!s.fb) return 'scene:screen'
    return s.vp[2] <= 16 ? 'scene:probe' : 'scene:capture'
  }
  const wrapProto = (proto) => {
    if (!proto) return
    const viewport = proto.viewport
    proto.viewport = function (x, y, w, h) { const s = state(this); s.vp = [x, y, w, h]; return viewport.call(this, x, y, w, h) }
    const bindFramebuffer = proto.bindFramebuffer
    proto.bindFramebuffer = function (target, fb) { const s = state(this); if (target !== this.READ_FRAMEBUFFER) s.fb = fb; return bindFramebuffer.call(this, target, fb) }
    for (const name of ['drawArrays', 'drawElements']) {
      const draw = proto[name]
      proto[name] = function (...a) {
        const s = state(this)
        if (stats.on && !s.ext) stats.draws[label(s) + ':untimed'] = (stats.draws[label(s) + ':untimed'] || 0) + 1
        if (!stats.on || !s.ext) return draw.apply(this, a)
        const gl = this
        const q = s.gl2 ? gl.createQuery() : s.ext.createQueryEXT()
        if (s.gl2) gl.beginQuery(s.ext.TIME_ELAPSED_EXT, q); else s.ext.beginQueryEXT(s.ext.TIME_ELAPSED_EXT, q)
        const result = draw.apply(this, a)
        if (s.gl2) gl.endQuery(s.ext.TIME_ELAPSED_EXT); else s.ext.endQueryEXT(s.ext.TIME_ELAPSED_EXT)
        const l = label(s)
        stats.draws[l] = (stats.draws[l] || 0) + 1
        pending.push({ s, q, l })
        return result
      }
    }
  }
  wrapProto(window.WebGLRenderingContext && WebGLRenderingContext.prototype)
  wrapProto(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype)
  const poll = () => {
    for (let i = pending.length - 1; i >= 0; i--) {
      const { s, q, l } = pending[i]
      const gl = s.gl
      if (gl.isContextLost()) { pending.splice(i, 1); continue }
      const ready = s.gl2 ? gl.getQueryParameter(q, gl.QUERY_RESULT_AVAILABLE) : s.ext.getQueryObjectEXT(q, s.ext.QUERY_RESULT_AVAILABLE_EXT)
      if (!ready) continue
      const ns = s.gl2 ? gl.getQueryParameter(q, gl.QUERY_RESULT) : s.ext.getQueryObjectEXT(q, s.ext.QUERY_RESULT_EXT)
      if (stats.on) stats.ms[l] = (stats.ms[l] || 0) + ns / 1e6
      if (s.gl2) gl.deleteQuery(q); else s.ext.deleteQueryEXT(q)
      pending.splice(i, 1)
    }
    requestAnimationFrame(poll)
  }
  requestAnimationFrame(poll)
})()`

function summarizeProfile(profile, title, frames) {
  const byId = new Map(profile.nodes.map(n => [n.id, n]))
  const parent = new Map()
  for (const n of profile.nodes) for (const c of n.children || []) parent.set(c, n.id)
  const selfUs = new Map()
  const totalUs = new Map()
  const deltas = profile.timeDeltas
  profile.samples.forEach((id, i) => {
    const dt = deltas[i + 1] ?? 0
    const n = byId.get(id)
    const f = n.callFrame
    const key = `${f.functionName || '(anon)'} ${f.url.split('/').pop()}:${f.lineNumber + 1}`
    selfUs.set(key, (selfUs.get(key) || 0) + dt)
    const seen = new Set()
    for (let cur = id; cur !== undefined; cur = parent.get(cur)) {
      const cf = byId.get(cur).callFrame
      const k = `${cf.functionName || '(anon)'} ${cf.url.split('/').pop()}:${cf.lineNumber + 1}`
      if (seen.has(k)) continue
      seen.add(k)
      totalUs.set(k, (totalUs.get(k) || 0) + dt)
    }
  })
  const fmt = (m, n) => [...m.entries()].filter(([k]) => !/^\((idle|program|root)\)/.test(k)).sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, us]) => `  ${(us / 1000 / frames).toFixed(3)} ms/frame  ${k}`).join('\n')
  const n = Number(args.top || 18)
  console.log(`--- profile: ${title} --- self:\n${fmt(selfUs, n)}\n total:\n${fmt(totalUs, n + 7)}`)
}

function summarizeTrace(events, title, frames) {
  const names = new Map()
  for (const e of events) if (e.ph === 'M' && e.name === 'thread_name') names.set(`${e.pid}:${e.tid}`, e.args.name)
  const threads = new Map()
  for (const e of events) {
    if (e.ph !== 'X' || typeof e.dur !== 'number') continue
    const key = `${e.pid}:${e.tid}`
    if (!threads.has(key)) threads.set(key, [])
    threads.get(key).push(e)
  }
  const keep = /CrRendererMain|CrGpuMain|VizCompositorThread|Compositor|GpuMain|DrDisplay/i
  console.log(`--- trace: ${title} (${Math.round(frames)} frames) ---`)
  for (const [key, list] of threads) {
    const name = names.get(key) || key
    if (!keep.test(name)) continue
    list.sort((a, b) => a.ts - b.ts || b.dur - a.dur)
    const self = new Map()
    const stack = []
    let busy = 0
    for (const e of list) {
      while (stack.length && stack[stack.length - 1].ts + stack[stack.length - 1].dur <= e.ts) stack.pop()
      const parent = stack[stack.length - 1]
      if (parent) self.set(parent.name, (self.get(parent.name) || 0) - e.dur)
      else busy += e.dur
      self.set(e.name, (self.get(e.name) || 0) + e.dur)
      stack.push(e)
    }
    if (/CrRendererMain/.test(name)) {
      const waits = list.filter(e => /WaitForGetOffset|WaitForToken|CommandBufferHelper::Finish/.test(e.name))
      const seconds = (list[list.length - 1].ts - list[0].ts) / 1e6
      const total = waits.reduce((n, e) => n + e.dur, 0) / 1000
      const max = waits.reduce((n, e) => Math.max(n, e.dur), 0) / 1000
      const over2 = waits.filter(e => e.dur > 2000).length
      console.log(`GPU waits on main: ${waits.length} (${(waits.length / seconds).toFixed(1)}/s), ${(total / seconds).toFixed(2)} ms/s, max ${max.toFixed(2)} ms, >2ms: ${over2}`)
    }
    const top = [...self.entries()].sort((a, b) => b[1] - a[1]).slice(0, Number(args.ttop || 12)).map(([n, us]) => `${n} ${(us / 1000 / frames).toFixed(3)}`)
    console.log(`${name} busy ${(busy / 1000 / frames).toFixed(2)} ms/frame | self ms/frame: ${top.join(' | ')}`)
  }
}

async function run(url, lit) {
  const profile = mkdtempSync(join(tmpdir(), 'plair-gpubench-'))
  const chrome = spawn(CHROME, [
    '--headless=new', ...gpuFlags, ...pacing,
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
    '--window-size=412,915', '--autoplay-policy=no-user-gesture-required', 'about:blank',
  ], { stdio: 'ignore' })
  let wsUrl
  for (let i = 0; i < 50 && !wsUrl; i++) {
    try { wsUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(p => p.type === 'page')?.webSocketDebuggerUrl } catch { /* starting */ }
    if (!wsUrl) await sleep(200)
  }
  const socket = new WebSocket(wsUrl)
  await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }))
  let nextId = 0
  const waiting = new Map()
  let traceEvents = []
  let traceDone = null
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data)
    if (message.id && waiting.has(message.id)) { waiting.get(message.id)(message); waiting.delete(message.id) }
    if (message.method === 'Tracing.dataCollected') traceEvents.push(...message.params.value)
    if (message.method === 'Tracing.tracingComplete') traceDone?.()
  })
  const send = (method, params = {}) => new Promise(resolve => { const id = ++nextId; waiting.set(id, resolve); socket.send(JSON.stringify({ id, method, params })) })
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (result.result?.exceptionDetails) throw new Error(JSON.stringify(result.result.exceptionDetails).slice(0, 400))
    return result.result?.result?.value
  }
  const results = []
  try {
    await send('Page.enable')
    await send('Runtime.enable')
    await send('Page.addScriptToEvaluateOnNewDocument', { source: args.notimer ? 'window.__gpuStats = { ms: {}, draws: {}, reset() {}, on: false }' : INSTRUMENT })
    await send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
      window.__rafDebug = { sources: {} }; const calls = window.__syncCalls = {}
      for (const proto of [WebGLRenderingContext.prototype, WebGL2RenderingContext.prototype]) {
        for (const name of ['getBufferSubData', 'readPixels', 'getError', 'finish', 'getSyncParameter', 'clientWaitSync', 'getParameter', 'getExtension', 'isContextLost', 'getProgramParameter', 'getShaderParameter', 'getUniformLocation', 'getActiveUniform']) {
          const fn = proto[name]
          if (!fn) continue
          proto[name] = function (...a) {
            const t = performance.now()
            const r = fn.apply(this, a)
            const d = performance.now() - t
            const c = calls[name] || (calls[name] = { n: 0, ms: 0, max: 0 })
            c.n++; c.ms += d; if (d > c.max) c.max = d
            return r
          }
        }
      }
    })()` })
    if (args.uniforms) await send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
      const forced = Object.fromEntries(${JSON.stringify(String(args.uniforms))}.split(',').map(kv => kv.split('=')).map(([k, v]) => [k, Number(v)]))
      for (const proto of [WebGLRenderingContext.prototype, WebGL2RenderingContext.prototype]) {
        const getLoc = proto.getUniformLocation
        proto.getUniformLocation = function (program, name) { const loc = getLoc.call(this, program, name); if (loc && name in forced) loc.__forced = forced[name]; return loc }
        const set1f = proto.uniform1f
        proto.uniform1f = function (loc, v) { return set1f.call(this, loc, loc && loc.__forced !== undefined ? loc.__forced : v) }
      }
    })()` })
    if (args.block) { await send('Network.enable'); await send('Network.setBlockedURLs', { urls: String(args.block).split(',') }) }
    if (args.css) await send('Page.addScriptToEvaluateOnNewDocument', { source: `document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style'); s.textContent = ${JSON.stringify(String(args.css))}; document.head.appendChild(s) })` })
    await send('Emulation.setDeviceMetricsOverride', { width: 412, height: 915, deviceScaleFactor: 2.625, mobile: true })
    await send('Emulation.setUserAgentOverride', { userAgent: UA, platform: 'Android' })
    await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 })
    await send('Emulation.setCPUThrottlingRate', { rate: CPU })
    await send('Page.navigate', { url })
    await sleep(4000)
    const renderer = await evaluate(`(() => { const gl = document.createElement('canvas').getContext('webgl'); const e = gl.getExtension('WEBGL_debug_renderer_info'); return gl.getParameter(e.UNMASKED_RENDERER_WEBGL) })()`)
    if (args.gpu && !renderer.includes(args.gpu)) throw new Error(`wrong GPU: ${renderer}`)
    await evaluate(`(() => {
      const saved = JSON.parse(localStorage.getItem('plair_settings') || '{}')
      localStorage.setItem('plair_settings', JSON.stringify({ ...saved, litArtwork: ${lit}, visualQuality: '${args.quality || 'high'}'${args.fps ? ', fpsEnabled: true' : ''} }))
      localStorage.setItem('plair_demo_mode_modal_seen', 'true')
    })()`)
    await send('Page.reload')
    await sleep(9000)
    for (const tab of TABS) {
      await evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${JSON.stringify(tab)}); if (b) b.click() })()`)
      await sleep(3000)
      if (args.fullscreen) {
        await evaluate(`document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }))`)
        await sleep(3000)
      }
      for (const mode of MODES) {
        for (let r = 0; r < REPEAT; r++) {
          const tilt = mode.includes('tilt')
          const level = mode.includes('light') ? 1 : 0
          let utilDone = null
          if (args.luid) {
            utilDone = new Promise(resolve => {
              let out = ''
              const ps = spawn('powershell', ['-NoProfile', '-File', join(import.meta.dirname, 'gpuutil.ps1'), '-Samples', String(Math.max(2, SECONDS - 2))])
              ps.stdout.on('data', d => { out += d })
              ps.on('close', () => { try { const v = [].concat(JSON.parse(out)); resolve(v.reduce((a, b) => a + b, 0) / v.length) } catch { resolve(null) } })
            })
          }
          if (args.profile) {
            await send('Profiler.enable')
            await send('Profiler.setSamplingInterval', { interval: 100 })
            await send('Profiler.start')
          }
          if (args.trace) {
            traceEvents = []
            await send('Tracing.start', { categories: 'toplevel,gpu,cc,viz,blink,v8.execute,devtools.timeline,disabled-by-default-devtools.timeline,disabled-by-default-gpu.service', transferMode: 'ReportEvents' })
          }
          const value = await evaluate(`new Promise(resolve => {
            window.__plairLight?.debug({ level: ${level} })
            window.__plairProfile = ${args.drawprofile ? '{}' : 'null'}
            for (const k in window.__syncCalls) delete window.__syncCalls[k]
            const stats = window.__gpuStats
            stats.reset(); stats.on = true
            const times = []
            const start = performance.now()
            let last = start
            let busy = 0
            const step = (now) => {
              const t = (now - start) / 1000
              if (${tilt}) window.dispatchEvent(Object.assign(new Event('deviceorientation'), { alpha: 0, beta: 20 * Math.sin(t * 1.3), gamma: 20 * Math.cos(t * 0.9) }))
              times.push(now - last)
              last = now
              if (now - start < ${SECONDS * 1000}) requestAnimationFrame(step)
              else setTimeout(() => {
                stats.on = false
                window.__plairLight?.debug({ level: null })
                const sorted = times.slice(5).sort((a, b) => a - b)
                const pick = q => +sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))].toFixed(2)
                const frames = sorted.length || 1
                const gpu = {}
                let total = 0
                for (const [k, v] of Object.entries(stats.ms)) { gpu[k] = +(v / frames).toFixed(3); total += v }
                const draws = {}
                for (const [k, v] of Object.entries(stats.draws)) draws[k] = +(v / frames).toFixed(2)
                const tiles = [...document.querySelectorAll('canvas[aria-hidden="true"]')].filter(c => c.style.opacity === '1')
                const tilePx = tiles.reduce((n, c) => n + c.width * c.height, 0)
                const secs = (now - start) / 1000
                const sync = Object.fromEntries(Object.entries(window.__syncCalls).filter(([, c]) => c.ms > 1).map(([k, c]) => [k, (c.n / secs).toFixed(0) + '/s ' + (c.ms / secs).toFixed(1) + 'ms/s max ' + c.max.toFixed(1)]))
                const modes = window.__plairProfile && { tiles: window.__plairProfile.tiles, nowPlaying: window.__plairProfile.nowPlaying }
                resolve({ modes, sync, fps: +(frames / ((now - start) / 1000)).toFixed(1), p50: pick(0.5), p95: pick(0.95), gpuTotalMs: +(total / frames).toFixed(3), gpu, draws, tiles: tiles.length, tileMpx: +(tilePx / 1e6).toFixed(2), quality: window.__plairQuality?.().name })
              }, 300)
            }
            requestAnimationFrame(step)
          })`)
          if (utilDone) {
            const util = await utilDone
            value.gpuUtil = util === null ? null : +util.toFixed(1)
            value.gpuBusyMs = util === null ? null : +(util / 100 * 1000 / value.fps).toFixed(3)
          }
          if (args.profile) {
            const { result } = await send('Profiler.stop')
            summarizeProfile(result.profile, `${tab} ${mode}`, value.fps * SECONDS)
          }
          if (args.trace) {
            const done = new Promise(resolve => { traceDone = resolve })
            await send('Tracing.end')
            await done
            summarizeTrace(traceEvents, `${tab} ${mode}`, value.fps * SECONDS)
          }
          results.push({ url, lit, tab, mode, ...value })
          console.log(JSON.stringify(results[results.length - 1]))
        }
      }
      if (args.eval) console.log('eval:', JSON.stringify(await evaluate(args.eval)))
      if (args.shots) {
        mkdirSync(args.shots, { recursive: true })
        if (args.shotlight) {
          await evaluate(`window.__plairLight?.debug({ level: 1, kick: 0 })`)
          await sleep(1500)
        }
        const shot = await send('Page.captureScreenshot', { format: 'png' })
        if (args.shotlight) await evaluate(`window.__plairLight?.debug({ level: null, kick: null })`)
        writeFileSync(join(args.shots, `${url.replace(/\W+/g, '_')}-${tab}-${lit ? 'lit' : 'flat'}.png`), Buffer.from(shot.result.data, 'base64'))
      }
    }
  } finally {
    socket.close()
    killProfileChrome(profile)
    await sleep(800)
    if (chrome.exitCode === null) await new Promise(r => { chrome.once("exit", r); setTimeout(r, 5000) }); try { rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 }) } catch (e) { console.error("profile cleanup failed", profile, e.message) }
  }
  return results
}

const all = []
for (const url of URLS) for (const lit of LIT) all.push(...await run(url, lit))
console.table(all.map(r => ({ url: r.url.slice(-4), lit: r.lit, tab: r.tab, mode: r.mode, fps: r.fps, p50: r.p50, p95: r.p95, util: r.gpuUtil, busyMs: r.gpuBusyMs, webglMs: r.gpuTotalMs, tilesMs: r.gpu.tiles, npMs: r.gpu.nowPlaying, sceneMs: r.gpu['scene:screen'], tiles: r.tiles })))

