import { page, sleep } from './cdp.mjs'
const p = await page()
const skip = async (label) => console.log(label, await p.evaluate(`(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  const frames = []; let on = true
  const tick = t => { frames.push(t); if (on) requestAnimationFrame(tick) }
  requestAnimationFrame(tick)
  await sleep(800)
  const title = navigator.mediaSession.metadata?.title
  document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' }))
  const t0 = performance.now()
  while (navigator.mediaSession.metadata?.title === title && performance.now() - t0 < 5000) await sleep(10)
  const change = performance.now()
  await sleep(1600)
  on = false
  const stat = (a, b) => { const w = frames.filter(t => t >= a && t < b); let worst = 0; for (let i = 1; i < w.length; i++) worst = Math.max(worst, w[i] - w[i - 1]); return { fps: +((w.length - 1) / (w.at(-1) - w[0]) * 1000).toFixed(1), worst: Math.round(worst) } }
  return JSON.stringify({ first: stat(change, change + 1000), next: stat(change + 1000, change + 1500) })
})()`))
const OFF = `(() => { const s = document.createElement('style'); s.id = 'no-transitions'; s.textContent = '*, *::before, *::after { transition: none !important }'; document.head.appendChild(s); return 1 })()`
const ON = `(() => { document.getElementById('no-transitions')?.remove(); return 1 })()`
for (let i = 0; i < 2; i++) {
  await p.evaluate(ON); await sleep(1500); await skip('fade on ')
  await p.evaluate(OFF); await sleep(1500); await skip('fade off')
}
await p.evaluate(ON)
p.close(); process.exit(0)
