// node oldnew.mjs [panels] [rounds] [seconds] — today's build against the 14 Nov morning behaviour, interleaved on the
// phone: "old" forces the cover atlas back to 3840x1024, puts the radio-button code back into the panel-interior
// shader and draws the background-only cells with the full shader. Frames per second at rest and under simulated tilt.
import { page, sleep, openPanel } from './cdp.mjs'

const panels = (process.argv[2] || 'Playing,Catalog,Queue').split(',')
const rounds = Number(process.argv[3] || 3)
const seconds = Number(process.argv[4] || 4)
const p = await page()

const measure = ms => p.evaluate(`new Promise(resolve => {
  const frames = []
  const end = performance.now() + ${ms}
  const tick = t => {
    frames.push(t)
    if (t < end) return requestAnimationFrame(tick)
    const gaps = []
    for (let i = 1; i < frames.length; i++) gaps.push(frames[i] - frames[i - 1])
    resolve(JSON.stringify({ fps: +((frames.length - 1) / (frames.at(-1) - frames[0]) * 1000).toFixed(1), over25ms: gaps.filter(g => g > 25).length }))
  }
  requestAnimationFrame(tick)
})`).then(JSON.parse)

const wobbleFor = async ms => {
  let on = true
  const start = Date.now()
  const loop = (async () => {
    while (on) {
      const t = (Date.now() - start) / 1000
      await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 10 * Math.sin(t * Math.PI * 1.6), gamma: 10 * Math.sin(t * Math.PI * 1.2 + 1) })
      await sleep(33)
    }
  })()
  await sleep(1000)
  const result = await measure(ms)
  on = false
  await loop
  return result
}

const setScene = old => p.evaluate(`window.__plairScene.eval(${JSON.stringify(`
  scene.__today = scene.__today || { interior: scene.glassInteriorMaterial.fragmentShader, backdrop: scene.glassBackdropMesh.material }
  const interior = ${old} ? scene.glassButtonMaterial.fragmentShader : scene.__today.interior
  if (scene.glassInteriorMaterial.fragmentShader !== interior) { scene.glassInteriorMaterial.fragmentShader = interior; scene.glassInteriorMaterial.needsUpdate = true }
  scene.glassBackdropMesh.material = ${old} ? scene.fgMaterial : scene.__today.backdrop
  return 'ok'
`)})`)
const setAtlas = old => p.evaluate(`(() => {
  clearInterval(window.__atlasHold)
  window.__atlasHold = ${old} ? setInterval(() => { const c = window.__atlasCanvas; if (c && (c.width !== 3840 || c.height !== 1024)) { c.width = 3840; c.height = 1024 } }, 100) : 0
  if (!${old} && window.__atlasCanvas && window.__atlasCanvas.width > 1024) window.__atlasCanvas.width = 1024
  return 1
})()`)

await p.evaluate(`(() => {
  const proto = CanvasRenderingContext2D.prototype
  if (!proto.__drawImage) proto.__drawImage = proto.drawImage
  proto.drawImage = function (source, ...rest) {
    if (source instanceof HTMLCanvasElement && !source.isConnected) window.__atlasCanvas = source
    return proto.__drawImage.call(this, source, ...rest)
  }
  window.__plairArt.set({ coarse: false })
  return 1
})()`)
const results = {}
try {
  for (const panel of panels) {
    await openPanel(p, panel)
    await sleep(2500)
    for (let r = 0; r < rounds; r++) {
      for (const old of [true, false]) {
        await setScene(old)
        await setAtlas(old)
        await sleep(2500)
        const rest = await measure(seconds * 1000)
        await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 0, gamma: 0 })
        const tilt = await wobbleFor(seconds * 1000)
        await p.send('DeviceOrientation.clearDeviceOrientationOverride')
        const key = `${panel} ${old ? 'old' : 'new'}`
        const entry = results[key] = results[key] || { rest: [], tilt: [] }
        entry.rest.push(rest.fps)
        entry.tilt.push(tilt.fps)
        console.log(`round ${r + 1} ${key.padEnd(14)} rest ${rest.fps} fps (${rest.over25ms} slow), tilt ${tilt.fps} fps (${tilt.over25ms} slow), atlas ${await p.evaluate(`window.__plairArt.stats().atlas`)}`)
      }
    }
  }
} finally {
  await setScene(false)
  await setAtlas(false)
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  await p.evaluate(`(() => { const proto = CanvasRenderingContext2D.prototype; if (proto.__drawImage) { proto.drawImage = proto.__drawImage; delete proto.__drawImage } window.__plairArt.set({ coarse: null }); return 1 })()`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
for (const [key, { rest, tilt }] of Object.entries(results)) console.log(key.padEnd(14), 'rest', median(rest), 'fps', `(${rest.join(', ')})`, '| tilt', median(tilt), 'fps', `(${tilt.join(', ')})`)
p.close()
process.exit(0)
