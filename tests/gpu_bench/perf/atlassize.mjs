import { page, sleep, openPanel } from './cdp.mjs'

const rounds = Number(process.argv[2] || 3)
const extra = Number(process.argv[3] || 2)
const sizes = (process.argv[4] || '3840x1024,1024x1024').split(',').map(s => s.split('x').map(Number))

const p = await page()
await openPanel(p, process.env.PANEL || 'Playing')
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await p.evaluate(`(() => {
  const proto = CanvasRenderingContext2D.prototype
  if (!proto.__drawImage) proto.__drawImage = proto.drawImage
  proto.drawImage = function (source, ...rest) {
    if (source instanceof HTMLCanvasElement && !source.isConnected && source.width >= 512) window.__atlasCanvas = source
    return proto.__drawImage.call(this, source, ...rest)
  }
  window.__plairLight.debug({ level: 0.6, kick: 0, pulse: 0 })
  window.__plairArt.set({ coarse: false, tilt: { x: 0.6, y: 0.4 }, bench: { forceFull: true, extraFull: ${extra}, extraCopy: 0 } })
  return 1
})()`)
await sleep(1500)
const measure = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const results = sizes.map(() => [])
try {
  for (let r = 0; r < rounds; r++) {
    for (const [index, [width, height]] of sizes.entries()) {
      const atlas = await p.evaluate(`(() => { const c = window.__atlasCanvas; if (!c) return 'no atlas seen'; c.width = ${width}; c.height = ${height}; return c.width + 'x' + c.height })()`)
      await sleep(800)
      await measure(700)
      const frame = await measure(3000)
      const now = await p.evaluate(`window.__atlasCanvas ? window.__atlasCanvas.width + 'x' + window.__atlasCanvas.height : ''`)
      results[index].push(frame)
      console.log(`round ${r + 1} atlas set ${atlas}, now ${now}: frame ${frame} ms`)
    }
  }
} finally {
  await p.evaluate(`(() => {
    const proto = CanvasRenderingContext2D.prototype
    if (proto.__drawImage) { proto.drawImage = proto.__drawImage; delete proto.__drawImage }
    window.__plairArt.set({ coarse: null, tilt: null, bench: { forceFull: false, extraFull: 0, extraCopy: 0 } })
    window.__plairLight.debug({ off: false, level: null, kick: null, pulse: null })
    return 1
  })()`)
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
for (const [index, [width, height]] of sizes.entries()) console.log(`${width}x${height}`.padEnd(12), 'median frame', median(results[index]), 'ms', results[index].join(', '))
p.close()
process.exit(0)
