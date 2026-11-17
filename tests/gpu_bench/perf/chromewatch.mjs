// Drags the open panel's list on the phone and records, per painted frame, the tab bar and player opacity, the panel area's
// bottom, the list's edge-mask lift and the scene's glass edge, so the scroll-away fades can be checked against each other.
// Usage: node chromewatch.mjs [panel]  (DIST, SHOT_AT, AFTER env). Screenshots rest/away/back go to E:/scratch/plair/chromewatch.
import { page, sleep, openPanel } from './cdp.mjs'
import { mkdirSync, writeFileSync } from 'fs'
const OUT = 'E:/scratch/plair/chromewatch'
mkdirSync(OUT, { recursive: true })
const p = await page()
await p.send('Page.enable')
console.log('open', await openPanel(p, process.argv[2] || 'Catalog'))
await sleep(1500)
const shot = async (name) => {
  const r = await p.send('Page.captureScreenshot', { format: 'jpeg', quality: 70 })
  writeFileSync(`${OUT}/${name}.jpg`, Buffer.from(r.result.data, 'base64'))
}
await shot('rest')
await p.evaluate(`(() => {
  const rec = window.__chromeRec = { rows: [], scene: [], on: true, height: innerHeight }
  const nav = document.querySelector('[data-mobile-nav]')
  const player = document.querySelector('[data-shader-panel="player"]')
  const viewport = document.querySelector('[data-mobile-viewport]')
  const frames = [...document.querySelectorAll('[data-scroller]')].map(s => s.parentElement)
  const visible = frames.find(f => { const r = f.getBoundingClientRect(); return r.left >= -1 && r.left < innerWidth / 2 })
  const t0 = performance.now()
  const channel = new MessageChannel()
  channel.port1.onmessage = () => {
    if (!rec.on) return
    const t = Math.round(performance.now() - t0)
    const m = (visible.style.maskImage.match(/transparent ([0-9.]+)px/) || [0, 0])[1]
    const nr = nav.getBoundingClientRect()
    rec.rows.push([t, +getComputedStyle(nav).opacity, +getComputedStyle(player).opacity, Math.round(viewport.getBoundingClientRect().bottom), Math.round(+m), Math.round(nr.top), nav.style.position || '-'])
    window.__plairScene.eval('return [scene.state.chromeReveal, scene.state.chromeLift, scene.panelRegionVecs[2].y - scene.panelRegionVecs[2].w / 2]').then(v => rec.scene.push([t, ...(v || [])]))
    requestAnimationFrame(() => channel.port2.postMessage(0))
  }
  requestAnimationFrame(() => channel.port2.postMessage(0))
  return 'rec'
})()`)
await sleep(300)
const rect = JSON.parse(await p.evaluate(`JSON.stringify((() => { const s = [...document.querySelectorAll('[data-scroller]')].find(e => { const r = e.getBoundingClientRect(); return r.left >= -1 && r.left < innerWidth / 2 && r.width > 0 }); const r = s.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height * 0.6 } })())`))
const gesture = p.send('Input.synthesizeScrollGesture', { x: Math.round(rect.x), y: Math.round(rect.y), yDistance: -Number(process.env.DIST || 900), speed: 1200, gestureSourceType: 'touch', repeatCount: 1, preventFling: true })
await sleep(Number(process.env.SHOT_AT || 700))
await shot('away')
await gesture
await sleep(Number(process.env.AFTER || 2000))
await shot('back')
const out = JSON.parse(await p.evaluate(`(() => { const r = window.__chromeRec; r.on = false; return JSON.stringify(r) })()`))
const sceneAt = t => { let best = null; for (const s of out.scene) if (s[0] <= t) best = s; return best }
let last = ''
for (const row of out.rows) {
  const s = sceneAt(row[0])
  const key = `nav ${row[1].toFixed(2)} player ${row[2].toFixed(2)} vpBottom ${row[3]} maskLift ${row[4]} navTop ${row[5]} ${row[6]} | scene reveal ${s ? (+s[1]).toFixed(2) : '?'} lift ${s ? Math.round(s[2]) : '?'} glassBottom ${s ? Math.round(s[3] * out.height) : "?"}px`
  if (key !== last) console.log(String(row[0]).padStart(5), key)
  last = key
}
console.log('frames', out.rows.length)
p.close()
