// CDP_PORT=9338 node buildcheck.mjs <out dir> <page part> [page part ...] - for each open page (headless.mjs), checks
// the cover renderer of that build: the GPU, cover textures, shader warnings and errors in the console, and saves the
// first Catalog tile with tilt pinned and lights off (deterministic between builds) plus one with lights on, so two
// builds can be compared pixel by pixel (compare the *_off.png files).
import { mkdirSync, writeFileSync } from 'node:fs'
import { page, sleep, openPanel } from './cdp.mjs'

const [outDir, ...parts] = process.argv.slice(2)
mkdirSync(outDir, { recursive: true })
for (const part of parts) {
  const p = await page(part)
  await p.send('Page.bringToFront')
  await sleep(1500)
  await p.send('Runtime.enable')
  const messages = []
  p.on(m => {
    if (m.method === 'Runtime.consoleAPICalled' && /warn|error/.test(m.params.type)) messages.push(m.params.args.map(a => a.value ?? a.description).join(' ').slice(0, 300))
    if (m.method === 'Runtime.exceptionThrown') messages.push('exception: ' + (m.params.exceptionDetails.exception?.description || '').slice(0, 300))
  })
  await openPanel(p, 'Catalog')
  await p.evaluate(`[...document.querySelectorAll('[data-scroller]')].forEach(s => s.scrollTop = 0), 1`)
  await p.evaluate(`window.__plairLight.debug({ off: true }), window.__plairArt.set({ coarse: false, bench: { forceFull: true, extraFull: 0, extraCopy: 0 }, tilt: { x: 0.6, y: 0.4 } }), 1`)
  await sleep(4000)
  const capture = suffix => p.evaluate(`(() => {
    const canvases = [...document.querySelectorAll('[data-scroller] canvas[role=img]')].filter(c => { const r = c.getBoundingClientRect(); return c.width > 200 && r.top >= 0 && r.bottom <= innerHeight && c.style.opacity !== '0' })
    canvases.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top || a.getBoundingClientRect().left - b.getBoundingClientRect().left)
    const canvas = canvases[0]
    if (!canvas) return ''
    return JSON.stringify({ size: canvas.width + 'x' + canvas.height, png: canvas.toDataURL('image/png') })
  })()`).then(json => {
    if (!json) return 'no tile'
    const { size, png } = JSON.parse(json)
    writeFileSync(`${outDir}/${part.replace(/\W+/g, '_')}_${suffix}.png`, Buffer.from(png.split(',')[1], 'base64'))
    return size
  })
  const off = await capture('off')
  await p.evaluate(`window.__plairLight.debug({ off: false, level: 0.6, kick: 0, pulse: 0 }), 1`)
  await sleep(2500)
  const on = await capture('on')
  const info = await p.evaluate(`(() => {
    const gl = document.createElement('canvas').getContext('webgl2')
    const debug = gl.getExtension('WEBGL_debug_renderer_info')
    const renderer = debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : '?'
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    const script = [...document.scripts].map(s => s.src).find(src => /assets\\/index-/.test(src)) || ''
    return JSON.stringify({ renderer, script: script.split('/').pop(), stats: window.__plairArt.stats(), light: window.__plairLight.read().lights.map(l => l.intensity) })
  })()`)
  await p.evaluate(`window.__plairLight.debug({ off: false, level: null, kick: null, pulse: null }), window.__plairArt.set({ coarse: null, bench: { forceFull: false, extraFull: 0, extraCopy: 0 }, tilt: null }), 1`)
  console.log(part, 'off', off, 'on', on, info)
  console.log('  console:', messages.length ? messages.slice(0, 8).join(' || ') : 'none')
  p.close()
}
process.exit(0)
