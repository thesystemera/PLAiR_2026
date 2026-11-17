// node sortswitch.mjs [modes] [wait ms] - taps the Catalog's sort buttons in turn (default Remastered, A-Z, Recent, Genre,
// Recent) on the phone and, after each tap, reports the list's scroll position, the cards on screen with a title against
// empty skeletons, the catalog requests made (live network events, not the capped resource timing buffer) and console
// errors. Ends on Recent.
import { page, sleep, openPanel } from './cdp.mjs'

const modes = (process.argv[2] || 'Remastered,A-Z,Recent,Genre,Recent').split(',')
const wait = Number(process.argv[3] || 3000)
const p = await page()
await p.send('Network.enable')
await p.send('Runtime.enable')
let requests = []
const errors = []
p.on(m => {
  if (m.method === 'Network.requestWillBeSent' && m.params.request.url.includes('/api/catalog')) requests.push(m.params.request.url.replace(/^https?:\/\/[^/]+/, '').slice(0, 100))
  if (m.method === 'Network.loadingFailed' && requests.length) requests.push('FAILED ' + m.params.errorText)
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map(a => a.value ?? a.description).join(' ').slice(0, 200))
})
await openPanel(p, 'Catalog')
await sleep(800)
const state = () => p.evaluate(`(() => {
  const scroller = [...document.querySelectorAll('[data-scroller]')].find(s => s.innerText.includes('TRACKS') || s.innerText.includes('GENRES'))
  if (!scroller) return 'no catalog list'
  const box = scroller.getBoundingClientRect()
  const cards = [...scroller.querySelectorAll('[class*="group/card"]')].filter(card => { const r = card.getBoundingClientRect(); return r.bottom > box.top + 120 && r.top < box.bottom })
  const titled = cards.filter(card => card.querySelector('h3')?.textContent.trim()).length
  return JSON.stringify({ top: Math.round(scroller.scrollTop), height: scroller.scrollHeight, cardsOnScreen: cards.length, withTitle: titled, firstText: scroller.innerText.slice(0, 50).split('\\n').join(' | ') })
})()`)
const tap = title => p.evaluate(`(() => { const b = document.querySelector('button[title="${title}"]'); if (!b) return 'no button'; b.click(); return 'tapped' })()`)
console.log('before', await state())
for (const mode of modes) {
  requests = []
  const result = await tap(mode)
  await sleep(wait)
  console.log(mode.padEnd(11), result, await state(), 'requests:', requests.length ? requests.join(' ; ') : 'none')
}
console.log('console errors:', errors.length ? errors.slice(0, 5).join(' || ') : 'none')
p.close()
process.exit(0)
