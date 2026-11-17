// node catstate.mjs [shot.jpg] - what the phone's Catalog shows right now, read-only: a screenshot, the open tab, each
// list's scroll position and first text, cover canvases by size, and the last catalog/shoutout requests with their age.
import { writeFileSync } from 'node:fs'
import { page } from './cdp.mjs'

const out = process.argv[2] || 'E:/scratch/plair/phone_now.jpg'
const p = await page()
const shot = await p.send('Page.captureScreenshot', { format: 'jpeg', quality: 45 })
writeFileSync(out, Buffer.from(shot.result.data, 'base64'))
console.log(await p.evaluate(`(() => {
  const now = performance.now()
  return JSON.stringify({
    visible: document.visibilityState,
    tabs: [...document.querySelectorAll('[data-mobile-nav] button')].map(b => b.innerText.trim().split('\\n')[0] + (b.getAttribute('aria-current') ? '*' : '')),
    lists: [...document.querySelectorAll('[data-scroller]')].map(s => ({ top: Math.round(s.scrollTop), height: s.scrollHeight, text: s.innerText.slice(0, 70).split('\\n').join(' | ') })),
    covers: window.__plairArt.stats().viewSizes,
    requests: performance.getEntriesByType('resource').filter(e => e.name.includes('/api/catalog') || e.name.includes('/api/shoutouts')).slice(-8).map(e => ({ url: e.name.replace(location.origin, '').slice(0, 90), secondsAgo: Math.round((now - e.startTime) / 1000), status: e.responseStatus })),
  }, null, 1)
})()`))
p.close()
process.exit(0)
