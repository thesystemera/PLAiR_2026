import { page } from './cdp.mjs'
const p = await page()
console.log(await p.evaluate(`(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  const entries = []
  const obs = new PerformanceObserver(list => entries.push(...list.getEntries()))
  obs.observe({ type: 'long-animation-frame', buffered: false })
  const title = navigator.mediaSession.metadata?.title
  document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' }))
  const clickAt = performance.now()
  let titleAt = null
  while (performance.now() - clickAt < 12000) { if (!titleAt && navigator.mediaSession.metadata?.title !== title) titleAt = performance.now(); if (titleAt && performance.now() - titleAt > 2500) break; await sleep(20) }
  obs.disconnect()
  const t0 = titleAt || clickAt
  return JSON.stringify(entries.filter(e => e.duration > 40).map(e => ({
    at: Math.round(e.startTime - t0), dur: Math.round(e.duration), block: Math.round(e.blockingDuration), render: Math.round(e.renderStart ? e.startTime + e.duration - e.renderStart : 0), style: Math.round(e.styleAndLayoutStart ? e.startTime + e.duration - e.styleAndLayoutStart : 0),
    scripts: e.scripts.filter(s => s.duration > 5).map(s => ({ d: Math.round(s.duration), inv: s.invoker.slice(0, 60), type: s.invokerType, fn: (s.sourceFunctionName || '').slice(0, 30), src: (s.sourceURL || '').split('/').pop().slice(0, 30) + ':' + s.sourceCharPosition, layout: Math.round(s.forcedStyleAndLayoutDuration) })),
  })), null, 0)
})()`))
p.close(); process.exit(0)
