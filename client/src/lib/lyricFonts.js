import registry from './lyricFonts.json'

const FONT_DIR = '/fonts/lyrics/'
const fonts = new Map(registry.fonts.map(font => [font.id, font]))
const faces = new Map()

function family(id) {
  return `plair-lyric-${id}`
}

function parseRange(range) {
  return range.split(',').map(part => {
    const [low, high] = part.trim().replace(/^U\+/i, '').split('-').map(value => parseInt(value, 16))
    return [low, Number.isFinite(high) ? high : low]
  })
}

function covers(ranges, codes) {
  return codes.some(code => ranges.some(([low, high]) => code >= low && code <= high))
}

function facesFor(id) {
  if (faces.has(id)) return faces.get(id)
  const font = fonts.get(id)
  const list = (font?.files || []).map(file => {
    const face = new FontFace(family(id), `url(${FONT_DIR}${file.file}) format('woff2')`,
      { style: file.style, weight: file.weight, unicodeRange: file.range, display: 'block' })
    document.fonts.add(face)
    return { face, ranges: parseRange(file.range), style: file.style }
  })
  faces.set(id, list)
  return list
}

export function loadLyricFonts(uses, text) {
  if (typeof FontFace === 'undefined' || typeof document === 'undefined' || !document.fonts) return Promise.resolve(false)
  const codes = [...new Set([...text.join('')].map(char => char.codePointAt(0)))]
  const wanted = []
  for (const [id, styles] of uses) {
    if (!fonts.has(id)) return Promise.resolve(false)
    for (const entry of facesFor(id)) {
      if (styles.has(entry.style) && covers(entry.ranges, codes)) wanted.push(entry.face)
    }
  }
  return Promise.all(wanted.map(face => face.load())).then(() => true, () => false)
}

export function lyricFont({ font, weight, italic }, px) {
  return `${italic ? 'italic ' : ''}${weight} ${px.toFixed(1)}px "${family(font)}", sans-serif`
}
