// node survey.mjs [panels] [seconds] — per panel: frames per second and slow frames with the phone at rest, then under
// simulated hand-held tilt. Switches panels on the phone. Panels are the tab labels (the Radio tab reads "Text" in text mode).
import { page, sleep } from './cdp.mjs'

const panels = (process.argv[2] || 'Queue,Catalog,Text,Playing').split(',')
const seconds = Number(process.argv[3] || 4)
const p = await page()

const measure = ms => p.evaluate(`new Promise(resolve => {
  const frames = []
  const long = []
  const observer = 'PerformanceObserver' in window && PerformanceObserver.supportedEntryTypes.includes('long-animation-frame')
    ? new PerformanceObserver(list => long.push(...list.getEntries().map(e => e.duration))) : null
  observer?.observe({ type: 'long-animation-frame' })
  const end = performance.now() + ${ms}
  const tick = t => {
    frames.push(t)
    if (t < end) return requestAnimationFrame(tick)
    observer?.disconnect()
    const gaps = []
    for (let i = 1; i < frames.length; i++) gaps.push(frames[i] - frames[i - 1])
    gaps.sort((a, b) => a - b)
    const at = q => +gaps[Math.min(gaps.length - 1, Math.floor(gaps.length * q))].toFixed(1)
    resolve(JSON.stringify({
      fps: +((frames.length - 1) / (frames.at(-1) - frames[0]) * 1000).toFixed(1),
      p50: at(0.5), p90: at(0.9), p99: at(0.99), worst: at(1),
      over25ms: gaps.filter(g => g > 25).length,
      longFrames: long.length, longMs: Math.round(long.reduce((a, b) => a + b, 0)),
    }))
  }
  requestAnimationFrame(tick)
})`)

const wobbleFor = async (ms) => {
  let on = true
  const start = Date.now()
  const loop = (async () => {
    while (on) {
      const t = (Date.now() - start) / 1000
      await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 10 * Math.sin(t * Math.PI * 1.6), gamma: 10 * Math.sin(t * Math.PI * 1.2 + 1) })
      await sleep(33)
    }
  })()
  await sleep(1200)
  const result = await measure(ms)
  on = false
  await loop
  return result
}

console.log('playing:', await p.evaluate(`navigator.mediaSession?.playbackState`), '| quality:', await p.evaluate(`JSON.stringify((({ level, smoothStep, levers }) => ({ level, smoothStep, levers }))(window.__plairQuality()))`))
try {
  for (const panel of panels) {
    const found = await p.evaluate(`(() => { const b = [...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim().startsWith(${JSON.stringify(panel)})); if (!b) return false; b.click(); return true })()`)
    if (!found) { console.log(panel, 'tab not found'); continue }
    await sleep(2000)
    console.log(panel.padEnd(8), 'rest', await measure(seconds * 1000))
    await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 0, gamma: 0 })
    console.log(panel.padEnd(8), 'tilt', await wobbleFor(seconds * 1000))
    await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  }
} finally {
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  console.log('quality after:', await p.evaluate(`JSON.stringify((({ level, smoothStep, levers }) => ({ level, smoothStep, levers }))(window.__plairQuality()))`))
}
p.close()
process.exit(0)
