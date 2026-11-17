// Scrolls the open panel's list to its very end on the phone (a real drag at the end), lets the tab bar and player come
// back, and checks that the end of the list is still in view above them; prints the list's end per painted frame.
// Usage: node chromeend.mjs [panel]
import { page, sleep, openPanel } from './cdp.mjs'
const p = await page()
console.log('open', await openPanel(p, process.argv[2] || 'Queue'))
await sleep(1200)
const find = `[...document.querySelectorAll('[data-scroller]')].find(e => { const r = e.getBoundingClientRect(); return r.left >= -1 && r.left < innerWidth / 2 && r.width > 0 })`
const rect = JSON.parse(await p.evaluate(`JSON.stringify((() => { const s = ${find}; s.scrollTop = Math.max(0, s.scrollHeight - s.clientHeight - 250); const r = s.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height * 0.6 } })())`))
await sleep(900)
await p.evaluate(`(() => {
  const s = ${find}
  const nav = document.querySelector('[data-mobile-nav]')
  const rec = window.__endRec = { rows: [], on: true }
  const channel = new MessageChannel()
  channel.port1.onmessage = () => {
    if (!rec.on) return
    const visibleBottom = nav.getBoundingClientRect().top
    const gap = Math.round(s.scrollHeight - s.clientHeight - s.scrollTop)
    rec.rows.push([Math.round(performance.now()), Math.round(s.getBoundingClientRect().bottom), gap, Math.round(visibleBottom), (s.lastElementChild.style.height || '0')])
    requestAnimationFrame(() => channel.port2.postMessage(0))
  }
  requestAnimationFrame(() => channel.port2.postMessage(0))
})()`)
await p.send('Input.synthesizeScrollGesture', { x: Math.round(rect.x), y: Math.round(rect.y), yDistance: -600, speed: 1200, gestureSourceType: 'touch', preventFling: true })
await sleep(2200)
const out = JSON.parse(await p.evaluate(`(() => { const r = window.__endRec; r.on = false; return JSON.stringify(r) })()`))
let last = ''
const t0 = out.rows[0][0]
for (const [t, bottom, gap, navTop, spacer] of out.rows) {
  const key = `list bottom ${bottom} gap-to-end ${gap} navTop ${navTop} spacer ${spacer}`
  if (key !== last) console.log(String(t - t0).padStart(5), key)
  last = key
}
console.log('end in view after return:', await p.evaluate(`(() => { const s = ${find}; const items = [...s.querySelectorAll('[data-index], [data-track-id], .group')]; return JSON.stringify({ gap: Math.round(s.scrollHeight - s.clientHeight - s.scrollTop), listBottom: Math.round(s.getBoundingClientRect().bottom), navTop: Math.round(document.querySelector('[data-mobile-nav]').getBoundingClientRect().top) }) })()`))
p.close()
