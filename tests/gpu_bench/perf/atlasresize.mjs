// node atlasresize.mjs [panel] [rounds] — what one resize of the cover atlas costs: frame intervals around a forced
// grow to 1088x1344 (the Catalog's size), against the same stretch of frames without one.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Radio'
const rounds = Number(process.argv[3] || 5)
const p = await page()
await openPanel(p, panel)
await p.evaluate(`(() => {
  const proto = CanvasRenderingContext2D.prototype
  if (!proto.__drawImage) proto.__drawImage = proto.drawImage
  proto.drawImage = function (source, ...rest) {
    if (source instanceof HTMLCanvasElement && !source.isConnected) window.__atlasCanvas = source
    return proto.__drawImage.call(this, source, ...rest)
  }
  return 1
})()`)
await sleep(2500)
const run = resize => p.evaluate(`new Promise(resolve => {
  const frames = []
  let syncMs = 0
  const tick = t => {
    frames.push(t)
    if (frames.length === 30 && ${resize}) {
      const c = window.__atlasCanvas
      const started = performance.now()
      c.width = 1088
      c.height = 1344
      syncMs = performance.now() - started
    }
    if (frames.length < 70) requestAnimationFrame(tick)
    else {
      const gaps = frames.slice(1).map((v, i) => v - frames[i])
      const around = gaps.slice(28, 36)
      const rest = [...gaps.slice(5, 28), ...gaps.slice(36)]
      resolve(JSON.stringify({ atlas: window.__atlasCanvas ? window.__atlasCanvas.width + 'x' + window.__atlasCanvas.height : null, syncMs: +syncMs.toFixed(2), worstAround: +Math.max(...around).toFixed(1), sumAround: +around.reduce((a, b) => a + b, 0).toFixed(1), worstRest: +Math.max(...rest).toFixed(1), meanRest: +(rest.reduce((a, b) => a + b, 0) / rest.length).toFixed(2) }))
    }
  }
  requestAnimationFrame(tick)
})`)
try {
  for (let r = 0; r < rounds; r++) {
    console.log('no resize  ', await run(false))
    console.log('with resize', await run(true))
    await sleep(2500)
  }
} finally {
  await p.evaluate(`(() => { const proto = CanvasRenderingContext2D.prototype; if (proto.__drawImage) { proto.drawImage = proto.__drawImage; delete proto.__drawImage } return 1 })()`)
}
p.close()
process.exit(0)
