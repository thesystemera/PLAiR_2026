// node scrollstrat.mjs [rounds] [distance] [speed] - two ways to lay out a virtual list, timed in a fling on the phone,
// outside the app (plain DOM, no React; the app's covers and background are paused). Cards copy the Catalog's markup
// (box, square art with a gradient, four round buttons, duration bar, title, subtitle), two per row, 222 px rows.
// 'window': today's VirtualScroller - one window translated to the first rendered row, cards placed in a grid by CSS
// order, so every card moves inside the window when a row comes or goes. 'rows': each row placed at its own fixed top,
// only the row that leaves is reused for the row that arrives. CANVAS=1 puts a drawn 2D canvas under each card's
// buttons, like the Catalog's cover canvases (each a composited layer). Interleaved rounds, medians of page updates a second.
import { page, sleep } from './cdp.mjs'

const rounds = Number(process.argv[2] || 3)
const distance = Number(process.argv[3] || 1800)
const speed = Number(process.argv[4] || 2500)
const p = await page()
const spot = JSON.parse(await p.evaluate(`JSON.stringify({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight * 0.45) })`))
console.log(await p.evaluate(`(() => {
  document.getElementById('strat-test')?.remove()
  window.__plairArt.set({ skipDraw: true })
  window.__plairScene.set({ skip: true })
  const ROWS = 1000, ROW = 222, PER = 2, AHEAD = 4
  const scroller = document.createElement('div')
  scroller.id = 'strat-test'
  scroller.style.cssText = 'position:fixed;inset:0;z-index:99999;overflow-y:auto;background:#0b0b10;color:#eee;font:13px system-ui'
  const list = document.createElement('div')
  list.style.cssText = 'position:relative;height:' + ROWS * ROW + 'px'
  scroller.appendChild(list)
  document.body.appendChild(scroller)
  const card = () => {
    const c = document.createElement('div')
    c.style.cssText = 'background:#16161d;border-radius:8px;padding:8px;height:' + (ROW - 10) + 'px;box-sizing:border-box;overflow:hidden'
    c.innerHTML = '<div style="position:relative;aspect-ratio:1;max-height:150px;margin:0 auto 8px;border-radius:8px;overflow:hidden;background:linear-gradient(135deg,#334,#223)">' +
      (${process.env.CANVAS ? 1 : 0} ? '<canvas width="320" height="320" style="position:absolute;inset:0;width:100%;height:100%"></canvas>' : '') + '<div style="position:absolute;left:8px;top:8px;display:flex;gap:6px">' + '<div style="width:24px;height:24px;border-radius:8px;background:rgba(0,0,0,.4)"></div>'.repeat(3) + '</div>' +
      '<div style="position:absolute;right:8px;top:8px;width:24px;height:24px;border-radius:8px;background:#2563eb"></div>' +
      '<div style="position:absolute;left:0;right:0;bottom:0;height:28px;background:linear-gradient(to top,rgba(0,0,0,.8),transparent);padding:6px 8px">3:21</div></div>' +
      '<div class="t" style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div><div class="s" style="opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div>'
    const canvas = c.querySelector('canvas')
    if (canvas) { const g = canvas.getContext('2d'); g.fillStyle = 'hsl(' + Math.random() * 360 + ',50%,40%)'; g.fillRect(0, 0, 320, 320) }
    return c
  }
  const fill = (c, i) => { c.querySelector('.t').textContent = 'Track number ' + i; c.querySelector('.s').textContent = 'Some style description for track ' + i }
  const visibleRows = Math.ceil(scroller.clientHeight / ROW) + 1
  let mode = null, state = null
  const setups = {
    window: () => {
      const win = document.createElement('div')
      win.style.cssText = 'position:absolute;top:0;left:0;right:0;will-change:transform'
      const grid = document.createElement('div')
      grid.style.cssText = 'display:grid;grid-template-columns:repeat(' + PER + ',1fr);column-gap:10px;padding:0 12px'
      win.appendChild(grid); list.appendChild(win)
      const slots = new Map()
      let start = -1, end = -1
      const update = () => {
        const first = Math.floor(scroller.scrollTop / ROW)
        const s = Math.max(0, first - AHEAD), e = Math.min(ROWS, first + visibleRows + AHEAD)
        if (s === start && e === end) return
        const free = []
        for (const [i, c] of slots) if (i < s * PER || i >= e * PER) { free.push(c); slots.delete(i) }
        for (let i = s * PER; i < e * PER; i++) if (!slots.has(i)) { const c = free.pop() || grid.appendChild(card()); fill(c, i); slots.set(i, c) }
        for (const c of free) c.remove()
        for (const [i, c] of slots) c.style.order = i
        win.style.transform = 'translateY(' + s * ROW + 'px)'
        start = s; end = e
      }
      return { update, remove: () => win.remove() }
    },
    rows: () => {
      const slots = new Map()
      let start = -1, end = -1
      const row = () => {
        const r = document.createElement('div')
        r.style.cssText = 'position:absolute;left:0;right:0;display:grid;grid-template-columns:repeat(' + PER + ',1fr);column-gap:10px;padding:0 12px'
        for (let k = 0; k < PER; k++) r.appendChild(card())
        return list.appendChild(r)
      }
      const update = () => {
        const first = Math.floor(scroller.scrollTop / ROW)
        const s = Math.max(0, first - AHEAD), e = Math.min(ROWS, first + visibleRows + AHEAD)
        if (s === start && e === end) return
        const free = []
        for (const [i, r] of slots) if (i < s || i >= e) { free.push(r); slots.delete(i) }
        for (let i = s; i < e; i++) if (!slots.has(i)) {
          const r = free.pop() || row()
          r.style.top = i * ROW + 'px'
          for (let k = 0; k < PER; k++) fill(r.children[k], i * PER + k)
          slots.set(i, r)
        }
        for (const r of free) r.remove()
        start = s; end = e
      }
      return { update, remove: () => { for (const r of slots.values()) r.remove() } }
    },
  }
  let raf = 0
  scroller.addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; state?.update() }) }, { passive: true })
  window.__strat = {
    use: name => { state?.remove(); scroller.scrollTop = ROWS * ROW / 2; mode = name; state = setups[name](); state.update(); return name },
    stop: () => { state?.remove(); scroller.remove(); window.__plairArt.set({ skipDraw: false }); window.__plairScene.set({ skip: false }); delete window.__strat },
  }
  return 'ready, ' + visibleRows + ' rows on screen'
})()`))
const fling = async () => {
  await p.evaluate(`(() => { const f = []; let last = performance.now(), on = true; const tick = t => { f.push(t - last); last = t; if (on) requestAnimationFrame(tick) }; requestAnimationFrame(tick); window.__stratFrames = { f, stop: () => { on = false } }; return 1 })()`)
  for (const direction of [-1, 1]) {
    const r = await p.send('Input.synthesizeScrollGesture', { x: spot.x, y: spot.y, yDistance: direction * distance, speed, gestureSourceType: 'touch' })
    if (r.error) throw new Error(r.error.message)
    await sleep(200)
  }
  return JSON.parse(await p.evaluate(`(() => { const w = window.__stratFrames; w.stop(); const g = w.f.slice(3); const total = g.reduce((a, b) => a + b, 0); return JSON.stringify({ fps: g.length / total * 1000, worst: Math.max(...g) }) })()`))
}
const results = { window: [], rows: [] }
try {
  for (let r = 0; r < rounds; r++) {
    for (const name of r % 2 ? ['rows', 'window'] : ['window', 'rows']) {
      await p.evaluate(`window.__strat.use('${name}')`)
      await sleep(700)
      const out = await fling()
      results[name].push(out)
      console.log(`round ${r + 1} ${name.padEnd(7)} ${out.fps.toFixed(1)} fps, worst ${out.worst.toFixed(0)} ms`)
    }
  }
} finally {
  await p.evaluate(`window.__strat?.stop(), 1`)
}
const median = list => [...list].sort((a, b) => a - b)[Math.floor(list.length / 2)]
for (const [name, list] of Object.entries(results)) console.log(`${name.padEnd(7)} median ${median(list.map(x => x.fps)).toFixed(1)} fps`)
p.close()
process.exit(0)
