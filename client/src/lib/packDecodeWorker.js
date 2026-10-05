import { decodePackHere, packBlursHere, packColorSamplesHere, packImageHere } from './packImage'

self.onmessage = async ({ data: { id, op, blob, map, radii } }) => {
  try {
    if (op === 'blurs') {
      self.postMessage({ id, result: await packBlursHere(blob, radii) })
    } else if (op === 'image') {
      self.postMessage({ id, result: await packImageHere(blob) })
    } else if (op === 'colors') {
      const samples = await packColorSamplesHere(blob)
      self.postMessage({ id, result: samples }, [samples.buffer])
    } else {
      const result = await decodePackHere(blob, map)
      self.postMessage({ id, result }, [result.color, result.map].filter(Boolean))
    }
  } catch (error) {
    self.postMessage({ id, error: String(error?.message || error) })
  }
}
