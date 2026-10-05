const DECODE_OPTIONS = { premultiplyAlpha: 'none', colorSpaceConversion: 'none' }
const IMAGE_QUALITY = 0.92
const COLOR_SAMPLES = 10000
const BLUR_QUALITY = 0.8

export async function decodePackHere(blob, map = true) {
  const packed = await createImageBitmap(blob, DECODE_OPTIONS)
  const half = Math.floor(packed.width / 2)
  try {
    const [color, depth] = await Promise.all([
      createImageBitmap(packed, 0, 0, half, packed.height),
      map ? createImageBitmap(packed, half, 0, half, packed.height) : null,
    ])
    return { color, map: depth }
  } finally {
    packed.close()
  }
}

async function colorCanvas(blob) {
  const { color } = await decodePackHere(blob, false)
  const canvas = new OffscreenCanvas(color.width, color.height)
  const context = canvas.getContext('2d', { willReadFrequently: true })
  context.drawImage(color, 0, 0)
  color.close()
  return { canvas, context }
}

export async function packImageHere(blob) {
  const { canvas } = await colorCanvas(blob)
  return canvas.convertToBlob({ type: 'image/jpeg', quality: IMAGE_QUALITY })
}

export async function packBlursHere(blob, radii) {
  const { color } = await decodePackHere(blob, false)
  try {
    return await Promise.all(radii.map((radius) => {
      const canvas = new OffscreenCanvas(color.width, color.height)
      const context = canvas.getContext('2d')
      context.filter = `blur(${radius}px)`
      context.drawImage(color, 0, 0)
      return canvas.convertToBlob({ type: 'image/jpeg', quality: BLUR_QUALITY })
    }))
  } finally {
    color.close()
  }
}

export async function packColorSamplesHere(blob) {
  const { canvas, context } = await colorCanvas(blob)
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
  const count = pixels.length / 4
  const stride = Math.max(1, Math.floor(count / COLOR_SAMPLES))
  const samples = new Uint8ClampedArray(Math.ceil(count / stride) * 4)
  for (let pixel = 0, out = 0; pixel < count; pixel += stride, out += 4) {
    samples[out] = pixels[pixel * 4]
    samples[out + 1] = pixels[pixel * 4 + 1]
    samples[out + 2] = pixels[pixel * 4 + 2]
    samples[out + 3] = 255
  }
  return samples
}

let worker = null
let jobs = 0
const pending = new Map()

function decodeWorker() {
  if (worker) return worker
  worker = new Worker(new URL('./packDecodeWorker.js', import.meta.url), { type: 'module' })
  worker.onmessage = ({ data }) => {
    const job = pending.get(data.id)
    if (!job) return
    pending.delete(data.id)
    if (data.error) job.reject(new Error(data.error))
    else job.resolve(data.result)
  }
  return worker
}

function inWorker(message) {
  return new Promise((resolve, reject) => {
    const id = ++jobs
    pending.set(id, { resolve, reject })
    decodeWorker().postMessage({ id, ...message })
  })
}

const onMainThread = typeof window !== 'undefined'

export function decodePack(blob, { map = true } = {}) {
  return onMainThread ? inWorker({ op: 'decode', blob, map }) : decodePackHere(blob, map)
}

export function packColorSamples(blob) {
  return inWorker({ op: 'colors', blob })
}

export async function packBlurUrls(blob, radii) {
  const blobs = await inWorker({ op: 'blurs', blob, radii })
  return blobs.map(image => URL.createObjectURL(image))
}

export async function packImageUrl(blob) {
  return URL.createObjectURL(await inWorker({ op: 'image', blob }))
}
