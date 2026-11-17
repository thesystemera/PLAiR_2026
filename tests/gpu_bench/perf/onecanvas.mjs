// node onecanvas.mjs install|remove|shot <out.jpg> - prototype of one cover canvas for the whole Catalog grid, in the
// live page, no build. 'install' defines window.__oneCanvas { start, stop }: start puts a single WebGL canvas inside the
// Catalog's scrolled list (so the compositor scrolls it with the cards between page updates), sized to the visible rows
// plus half a row above and below, and every page frame moves it to the scroll position and redraws a quad over every
// cover on screen (tinted, changing every frame, like covers in a fling). Pair it with __plairArt.set({ skipCopy: true })
// to freeze the per-card canvases: that is the cost of the one-canvas design without its shader work moving over.
// 'shot' starts it, scrolls slowly and saves a screenshot mid-scroll to check the quads stay on the covers.
import { writeFileSync } from 'node:fs'
import { page, sleep, openPanel } from './cdp.mjs'

const [command = 'install', out = 'E:/scratch/plair/onecanvas.jpg'] = process.argv.slice(2)
const p = await page()

const INSTALL = `(() => {
  window.__oneCanvas?.stop()
  const state = { running: false }
  const start = () => {
    if (state.running) return 'running'
    const scroller = [...document.querySelectorAll('[data-scroller]')].find(s => s.querySelector('button[title="Recent"]') && s.querySelector('[data-virtual-window]'))
    if (!scroller) return 'no catalog list'
    const list = scroller.querySelector('[data-virtual-window]').parentElement
    const dpr = devicePixelRatio
    const canvas = document.createElement('canvas')
    canvas.id = 'one-canvas'
    const rowCss = 222
    const margin = rowCss / 2
    const cssHeight = scroller.clientHeight + 2 * margin
    canvas.width = Math.round(list.clientWidth * dpr)
    canvas.height = Math.round(cssHeight * dpr)
    canvas.style.cssText = 'position:absolute;left:0;top:0;width:' + list.clientWidth + 'px;height:' + cssHeight + 'px;pointer-events:none;z-index:5;will-change:transform'
    list.appendChild(canvas)
    const gl = canvas.getContext('webgl2', { antialias: false, alpha: true, premultipliedAlpha: true, preserveDrawingBuffer: false })
    const VS = '#version 300 es\\nin vec2 p; uniform vec4 r; out vec2 uv; void main() { uv = p; gl_Position = vec4(r.xy + p * r.zw, 0.0, 1.0); }'
    const FS = '#version 300 es\\nprecision highp float; in vec2 uv; uniform float t; uniform float k; out vec4 o;' +
      'void main() { float v = 0.5 + 0.5 * sin(uv.x * 12.0 + t * 3.0 + k) * cos(uv.y * 9.0 - t * 2.0); o = vec4(v * 0.6, 0.1, (1.0 - v) * 0.6, 0.6); }'
    const make = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s }
    const program = gl.createProgram()
    gl.attachShader(program, make(gl.VERTEX_SHADER, VS)); gl.attachShader(program, make(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(program); gl.useProgram(program)
    const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]), gl.STATIC_DRAW)
    const at = gl.getAttribLocation(program, 'p'); gl.enableVertexAttribArray(at); gl.vertexAttribPointer(at, 2, gl.FLOAT, false, 0, 0)
    const uR = gl.getUniformLocation(program, 'r'), uT = gl.getUniformLocation(program, 't'), uK = gl.getUniformLocation(program, 'k')
    state.frames = 0
    const tick = now => {
      if (!state.running) return
      const listBox = list.getBoundingClientRect()
      const box = scroller.getBoundingClientRect()
      const top = Math.max(0, box.top - listBox.top - margin)
      canvas.style.transform = 'translateY(' + top + 'px)'
      const originY = listBox.top + top
      gl.viewport(0, 0, canvas.width, canvas.height)
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT)
      gl.uniform1f(uT, now / 1000)
      let k = 0
      for (const art of scroller.querySelectorAll('[data-depth-art]')) {
        const r = art.getBoundingClientRect()
        if (r.bottom < originY || r.top > originY + cssHeight || r.width < 40) continue
        const x = (r.left - listBox.left) / list.clientWidth * 2 - 1
        const y = 1 - (r.bottom - originY) / cssHeight * 2
        gl.uniform4f(uR, x, y, r.width / list.clientWidth * 2, r.height / cssHeight * 2)
        gl.uniform1f(uK, k++)
        gl.drawArrays(gl.TRIANGLES, 0, 6)
      }
      state.frames++
      requestAnimationFrame(tick)
    }
    state.running = true
    state.stop = () => { state.running = false; gl.getExtension('WEBGL_lose_context')?.loseContext(); canvas.remove() }
    requestAnimationFrame(tick)
    return 'started ' + canvas.width + 'x' + canvas.height
  }
  const stop = () => { if (state.running) state.stop(); state.running = false; return 'stopped' }
  window.__oneCanvas = { start, stop, state }
  return 'installed'
})()`

if (command === 'remove') {
  console.log(await p.evaluate(`(() => { window.__oneCanvas?.stop(); delete window.__oneCanvas; window.__plairArt.set({ skipCopy: false }); return 'removed' })()`))
} else {
  console.log(await p.evaluate(INSTALL))
  if (command === 'shot') {
    await openPanel(p, 'Catalog')
    await sleep(800)
    console.log(await p.evaluate('window.__oneCanvas.start()'))
    const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
    const gesture = p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: -1500, speed: 2500, gestureSourceType: 'touch' })
    await sleep(350)
    const shot = await p.send('Page.captureScreenshot', { format: 'jpeg', quality: 50 })
    writeFileSync(out, Buffer.from(shot.result.data, 'base64'))
    await gesture
    console.log('saved', out, await p.evaluate('window.__oneCanvas.state.frames'))
    console.log(await p.evaluate('window.__oneCanvas.stop()'))
  }
}
p.close()
process.exit(0)
