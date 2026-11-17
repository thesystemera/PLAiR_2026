const PALETTE_DARK = 0.06
const PALETTE_LIGHT = 0.9

function withLuma(rgb, target) {
  const luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
  if (luma < 1e-4) return [target, target, target]
  return rgb.map(c => Math.min(1, c * target / luma))
}

export function coverPalette(data) {
  const count = data.length / 4
  if (!count) return null
  const lumaOf = i => (77 * data[i] + 150 * data[i + 1] + 29 * data[i + 2]) >> 8
  const histogram = new Uint32Array(256)
  for (let i = 0; i < data.length; i += 4) histogram[lumaOf(i)]++
  const percentile = q => {
    let need = count * q
    for (let v = 0; v < 256; v++) {
      need -= histogram[v]
      if (need <= 0) return v
    }
    return 255
  }
  const darkCut = percentile(0.25)
  const brightCut = percentile(0.5)
  const dark = [0, 0, 0]
  const light = [0, 0, 0]
  let darkCount = 0
  let lightWeight = 0
  for (let i = 0; i < data.length; i += 4) {
    const luma = lumaOf(i)
    const r = data[i] / 255
    const g = data[i + 1] / 255
    const b = data[i + 2] / 255
    if (luma <= darkCut) {
      dark[0] += r
      dark[1] += g
      dark[2] += b
      darkCount++
    }
    if (luma >= brightCut) {
      const high = Math.max(r, g, b)
      const saturation = high > 0 ? (high - Math.min(r, g, b)) / high : 0
      const weight = saturation * saturation + 0.02
      light[0] += r * weight
      light[1] += g * weight
      light[2] += b * weight
      lightWeight += weight
    }
  }
  return {
    dark: withLuma(dark.map(c => c / Math.max(1, darkCount)), PALETTE_DARK),
    light: withLuma(light.map(c => c / Math.max(1e-6, lightWeight)), PALETTE_LIGHT),
  }
}
