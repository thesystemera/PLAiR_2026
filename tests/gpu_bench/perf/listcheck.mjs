// node listcheck.mjs [out dir] - checks every mounted VirtualScroller list on the phone: per list the rows mounted, rows
// that overlap the row before them, the largest gap between consecutive rows (rows are placed one after another, so
// both should be about 0), and the first text. Opens Shoutouts (taps the open Catalog tab, then again to switch back)
// and the Radio Timeline (header button, closed again) to check those too, and saves a screenshot of each list.
import { writeFileSync, mkdirSync } from 'node:fs'
import { page, sleep, openPanel } from './cdp.mjs'

const out = process.argv[2] || 'E:/scratch/plair/lists'
mkdirSync(out, { recursive: true })
const p = await page()
const check = () => p.evaluate(`(() => [...document.querySelectorAll('[data-virtual-window]')].map(win => {
  const scroller = win.closest('[data-scroller]') || win.parentElement
  const box = scroller.getBoundingClientRect()
  const rows = [...win.children].filter(r => !r.dataset.motionPopId).map(r => r.getBoundingClientRect()).filter(r => r.height > 0).sort((a, b) => a.top - b.top)
  let overlaps = 0, maxGap = 0
  for (let i = 1; i < rows.length; i++) {
    const gap = rows[i].top - rows[i - 1].bottom
    if (gap < -1) overlaps++
    maxGap = Math.max(maxGap, gap)
  }
  return { panelLeft: Math.round(box.left), rows: rows.length, overlaps, maxGap: Math.round(maxGap * 10) / 10, rowHeights: rows.slice(0, 3).map(r => Math.round(r.height)), text: scroller.innerText.slice(0, 50).replace(/\\s+/g, ' ') }
}))()`)
const shot = async name => {
  const s = await p.send('Page.captureScreenshot', { format: 'jpeg', quality: 40 })
  writeFileSync(`${out}/${name}.jpg`, Buffer.from(s.result.data, 'base64'))
}
const report = async label => {
  for (const list of await check()) console.log(label.padEnd(10), JSON.stringify(list))
}
await report('mounted')
for (const panel of ['Queue', 'Radio', 'Catalog']) {
  console.log(panel, await openPanel(p, panel))
  await sleep(1500)
  await shot(panel)
}
const tapCatalog = () => p.evaluate(`(() => { const tab = [...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim().startsWith('Catalog') || b.innerText.trim().startsWith('Shout')); tab.click(); return tab.innerText.trim().split('\\n')[0] })()`)
console.log('tap open tab ->', await tapCatalog())
await sleep(2500)
await report('shoutouts')
await shot('Shoutouts')
console.log('tap again ->', await tapCatalog())
await sleep(1500)
console.log('Radio', await openPanel(p, 'Radio'))
await sleep(800)
console.log('timeline', await p.evaluate(`(() => { const b = document.querySelector('button[aria-label="Timeline"]'); if (!b) return 'no button'; b.click(); return 'opened' })()`))
await sleep(2500)
await report('timeline')
await shot('Timeline')
console.log('timeline', await p.evaluate(`(() => { const b = document.querySelector('button[aria-label="Timeline"]'); b.click(); return 'closed' })()`))
await sleep(800)
console.log('Catalog', await openPanel(p, 'Catalog'))
p.close()
process.exit(0)
