// node touchfps.mjs [panel] [seconds] [x,y] — frames per second with no touch, then with a synthetic finger held on
// the screen and moving by a pixel (Chrome on Android holds page frames to 60 Hz unless the screen is touched). The touch is cancelled, not
// lifted, so nothing is tapped. Default spot: the panel header.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2]
const seconds = Number(process.argv[3] || 3)
const [x, y] = (process.argv[4] || '120,28').split(',').map(Number)
const p = await page()
if (panel) {
  await openPanel(p, panel)
  await sleep(2000)
}
const measure = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else { const d = []; for (let i = 1; i < f.length; i++) d.push(f[i] - f[i - 1]); d.sort((a, b) => a - b); resolve(JSON.stringify({ fps: +((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1), p50: +d[Math.floor(d.length / 2)].toFixed(1), p90: +d[Math.floor(d.length * 0.9)].toFixed(1) })) } }; requestAnimationFrame(tick) })`)
console.log('no touch  ', await measure(seconds * 1000))
await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] })
let moving = true
const jitter = (async () => {
  let step = 0
  while (moving) {
    await p.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + (step++ % 2), y }] })
    await sleep(8)
  }
})()
try {
  await sleep(300)
  console.log('touch held', await measure(seconds * 1000))
} finally {
  moving = false
  await jitter
  await p.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] })
}
console.log('after     ', await measure(1500))
p.close()
process.exit(0)
