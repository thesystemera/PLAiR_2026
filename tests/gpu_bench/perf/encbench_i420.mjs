// node encbench_i420.mjs [frames] — the phone's video encoders fed ready-made I420 frames from memory (no canvas, no
// GPU readback), in a worker: frames a second and bitrate for hardware / software, 720x1280 at 30 fps, 1 Mbps asked.
// The picture is a scrolling gradient with moving blocks, so the numbers are a guide, not the app's content.
import { page } from './cdp.mjs'

const frames = Number(process.argv[2] || 300)
const p = await page()
const ALL = {
  'high, hardware': { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware' },
  'high, software': { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-software' },
  'high, default': { codec: 'avc1.64001f' },
  'high, software, realtime': { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-software', latencyMode: 'realtime' },
  'high, hardware, 2.5 Mbps asked': { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware', bitrate: 2500000 },
  'high, software, 2.5 Mbps asked': { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-software', bitrate: 2500000 },
  'high, hardware, constant 1 Mbps': { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware', bitrateMode: 'constant' },
  'high, hardware, constant 2 Mbps': { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware', bitrateMode: 'constant', bitrate: 2000000 },
  'high, hardware, constant 3 Mbps': { codec: 'avc1.64001f', hardwareAcceleration: 'prefer-hardware', bitrateMode: 'constant', bitrate: 3000000 },
  'main, hardware, constant 2 Mbps': { codec: 'avc1.4d001f', hardwareAcceleration: 'prefer-hardware', bitrateMode: 'constant', bitrate: 2000000 },
}
const wanted = process.argv[3] ? process.argv[3].split('|') : Object.keys(ALL)
const configs = wanted.map(name => [name, ALL[name]])
const workerSource = `
self.onmessage = async ({ data }) => {
  const { config, total } = data
  const width = 720, height = 1280, fps = 30
  const ySize = width * height, cSize = (width / 2) * (height / 2)
  const variants = []
  for (let v = 0; v < 12; v++) {
    const buffer = new Uint8Array(ySize + 2 * cSize)
    for (let y = 0; y < height; y++) {
      const row = y * width
      for (let x = 0; x < width; x++) {
        let value = ((x + v * 9) * 0.25 + (y + v * 23) * 0.18) & 255
        const bx = (x + v * 31) % 240, by = (y + v * 57) % 320
        if (bx < 90 && by < 40) value = 255 - value
        buffer[row + x] = 40 + (value >> 1) + (((x * 7 + y * 13 + v) & 7))
      }
    }
    for (let i = 0; i < cSize; i++) {
      buffer[ySize + i] = 110 + ((i + v * 500) >> 9 & 31)
      buffer[ySize + cSize + i] = 120 + ((i * 3 + v * 900) >> 10 & 31)
    }
    variants.push(buffer)
  }
  let bytes = 0, chunks = 0, failure = null
  const encoder = new VideoEncoder({ output: chunk => { bytes += chunk.byteLength; chunks++ }, error: e => { failure = e.message || String(e) } })
  try { encoder.configure({ width, height, bitrate: 1000000, framerate: fps, ...config }) } catch (e) { self.postMessage('configure failed: ' + e.message); return }
  const started = performance.now()
  for (let i = 0; i < total && !failure; i++) {
    const frame = new VideoFrame(variants[i % variants.length], { format: 'I420', codedWidth: width, codedHeight: height, timestamp: Math.round(i / fps * 1e6), duration: Math.round(1e6 / fps) })
    encoder.encode(frame, { keyFrame: i % 60 === 0 })
    frame.close()
    while (encoder.encodeQueueSize > 4 && !failure) await new Promise(r => { const t = setTimeout(r, 50); encoder.addEventListener('dequeue', () => { clearTimeout(t); r() }, { once: true }) })
  }
  if (!failure) await encoder.flush().catch(e => { failure = e.message })
  const seconds = (performance.now() - started) / 1000
  encoder.close()
  self.postMessage(failure ? 'failed: ' + failure : (total / seconds).toFixed(1) + ' frames/s (' + (seconds * 1000 / total).toFixed(1) + ' ms a frame), ' + (bytes * 8 / (total / fps) / 1e6).toFixed(2) + ' Mbps')
}
`
for (const [label, config] of configs) {
  const out = await p.evaluate(`new Promise(resolve => {
    const url = URL.createObjectURL(new Blob([${JSON.stringify(workerSource)}], { type: 'text/javascript' }))
    const worker = new Worker(url)
    const done = text => { worker.terminate(); URL.revokeObjectURL(url); resolve(text) }
    worker.onmessage = e => done(e.data)
    worker.onerror = e => done('worker error: ' + e.message)
    worker.postMessage({ config: ${JSON.stringify(config)}, total: ${frames} })
  })`)
  console.log(label.padEnd(34), out)
}
p.close()
process.exit(0)
