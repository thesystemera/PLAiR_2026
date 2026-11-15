export const numericAscending = (a, b) => a - b

export function lowerBound(sorted, value) {
  let lo = 0
  let hi = sorted.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (sorted[mid] < value) lo = mid + 1
    else hi = mid
  }
  return lo
}

export function processLyricTimestamps(data) {
  if (!data?.lyrics) return []
  const words = []
  for (const line of data.lyrics) {
    for (const word of line.words || []) {
      words.push({
        text: word.word,
        start: word.start,
        end: word.end,
      })
    }
  }
  return words
}

export function getLyricAt(lyrics, timeMs, startIndex = 0) {
  if (!lyrics?.length) return null
  const t = timeMs / 1000
  for (let i = startIndex; i < lyrics.length; i++) {
    const w = lyrics[i]
    if (t >= w.start && t <= w.end) return w.text
    if (w.start > t) break
  }
  return null
}

export function renderLyricToCanvas(ctx, text, width, height) {
  ctx.clearRect(0, 0, width, height)
  if (text) {
    ctx.fillStyle = 'white'
    ctx.font = '900 180px Inter, sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(text, width / 2, height / 2)
  }
}
