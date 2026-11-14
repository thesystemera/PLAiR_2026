// node encbench.mjs [frames] — how fast the phone's video encoders take 720x1280 frames and how many bits they spend:
// the same moving test picture through each WebCodecs configuration (hardware / software / default, key frame spacing,
// bitrate mode). No app code involved; nothing is uploaded.
import { page } from './cdp.mjs'

const frames = Number(process.argv[2] || 240)
const p = await page()
const configs = [
  ['high, default', { codec: 'avc1.64001f' }, 30],
  ['high, hardware', { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware' }, 30],
  ['high, software', { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-software' }, 30],
  ['high, hardware, key frame every 2 s', { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware' }, 60],
  ['high, hardware, constant bitrate', { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware', bitrateMode: 'constant' }, 30],
  ['high, hardware, constant, key 2 s', { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware', bitrateMode: 'constant' }, 60],
  ['high, hardware, realtime', { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware', latencyMode: 'realtime' }, 30],
  ['main, hardware', { codec: 'avc1.4d001f', hardwareAcceleration: 'prefer-hardware' }, 30],
  ['baseline, hardware', { codec: 'avc1.42001f', hardwareAcceleration: 'prefer-hardware' }, 30],
]
for (const [label, config, keyEvery] of configs) {
  const out = await p.evaluate(`(async () => {
    const width = 720, height = 1280, fps = 30, total = ${frames}
    const canvas = new OffscreenCanvas(width, height)
    const ctx = canvas.getContext('2d')
    let bytes = 0
    let chunks = 0
    let failure = null
    const encoder = new VideoEncoder({ output: chunk => { bytes += chunk.byteLength; chunks++ }, error: e => { failure = e.message || String(e) } })
    try {
      encoder.configure({ width, height, bitrate: 1000000, framerate: fps, ...${JSON.stringify(config)} })
    } catch (e) { return 'configure failed: ' + e.message }
    let seed = 7
    const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647
    const started = performance.now()
    for (let i = 0; i < total && !failure; i++) {
      const hue = (i * 3) % 360
      const g = ctx.createLinearGradient(0, 0, width, height)
      g.addColorStop(0, 'hsl(' + hue + ' 70% 35%)')
      g.addColorStop(1, 'hsl(' + ((hue + 120) % 360) + ' 70% 20%)')
      ctx.fillStyle = g
      ctx.fillRect(0, 0, width, height)
      for (let k = 0; k < 60; k++) {
        ctx.fillStyle = 'hsla(' + Math.round(rand() * 360) + ' 80% 60% / 0.5)'
        ctx.fillRect(rand() * width, ((rand() * height) + i * 6) % height, 20 + rand() * 120, 6 + rand() * 40)
      }
      ctx.fillStyle = 'white'
      ctx.font = '900 150px sans-serif'
      ctx.fillText('WORD ' + (i >> 4), 60 + (i % 40), 640)
      const frame = new VideoFrame(canvas, { timestamp: Math.round(i / fps * 1e6), duration: Math.round(1e6 / fps) })
      encoder.encode(frame, { keyFrame: i % ${keyEvery} === 0 })
      frame.close()
      while (encoder.encodeQueueSize > 4 && !failure) await new Promise(r => { const t = setTimeout(r, 50); encoder.addEventListener('dequeue', () => { clearTimeout(t); r() }, { once: true }) })
    }
    if (!failure) await encoder.flush().catch(e => { failure = e.message })
    const seconds = (performance.now() - started) / 1000
    encoder.close()
    if (failure) return 'failed: ' + failure
    return (total / seconds).toFixed(1) + ' frames/s (' + (seconds * 1000 / total).toFixed(1) + ' ms a frame), ' + (bytes * 8 / (total / fps) / 1e6).toFixed(2) + ' Mbps, ' + chunks + ' chunks'
  })()`)
  console.log(label.padEnd(38), out)
}
p.close()
process.exit(0)
