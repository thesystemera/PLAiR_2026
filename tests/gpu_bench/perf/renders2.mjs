import { page, sleep } from './cdp.mjs'
import { readFileSync } from 'node:fs'
const p = await page()
await p.send('Page.enable')
await p.send('Page.addScriptToEvaluateOnNewDocument', { source: readFileSync('renderhook.js', 'utf8') })
await p.send('Page.reload', { ignoreCache: true })
await sleep(12000)
console.log(await p.evaluate(`(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  ;[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(process.argv[2] || 'Playing')}).click()
  await sleep(2500)
  window.__renderLog.length = 0
  window.__renderRoots = new Map()
  window.__renderWatch = true
  document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' }))
  await sleep(2500)
  window.__renderWatch = false
  const total = window.__renderLog.reduce((a, c) => a + c.total, 0)
  return JSON.stringify({ commits: window.__renderLog.length, renders: total, roots: [...window.__renderRoots.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25).map(([k, v]) => v + '  ' + k) }, null, 1)
})()`))
p.close(); process.exit(0)
