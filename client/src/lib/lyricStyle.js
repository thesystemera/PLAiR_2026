import { lyricFont } from './lyricFonts'

const SIZE_SCALE = [0, 0.45, 0.62, 0.8, 1.05, 1.35, 1.75]
const UNIT = 0.22
const BOX_WIDTH = 0.86
const BOX_HEIGHT = 0.68
const HOLD_S = 1.2
const WORD_GAP = 0.26
const LINE_GAP = 0.12
const STACK_LINE_GAP = 0.05
const STACK_WIDTH = 0.8
const STACK_LINE_MAX = 0.42
const CASCADE_STEP = 0.1
const ALIGN_SHIFT = 0.06
const GROW = { solo: 2.2, stack: 1, flow: 1.3, cascade: 1.3 }
const PAST_ALPHA = 0.8

function applyCase(text, mode) {
  if (mode === 'upper') return text.toLocaleUpperCase()
  if (mode === 'lower') return text.toLocaleLowerCase()
  if (mode === 'title') return text.charAt(0).toLocaleUpperCase() + text.slice(1)
  return text
}

export function prepareLyricStyle(timestamps) {
  const style = timestamps?.style
  const timed = (timestamps?.lyrics || []).flatMap(line => line.words || [])
  if (!style?.cards?.length || timed.length !== style.word_count) return null
  const uses = new Map()
  const text = []
  const cards = style.cards.map(card => {
    const words = []
    let line = 0
    for (let index = card.start; index <= card.end; index++) {
      if (index > card.start && card.breaks.includes(index)) line++
      const own = card.words[String(index)] || {}
      const look = { font: own.font ?? card.font, weight: own.weight ?? card.weight, italic: own.italic ?? card.italic, size: own.size ?? card.size }
      const shown = applyCase(style.text?.[index] ?? timed[index].word, own.case ?? card.case)
      if (!uses.has(look.font)) uses.set(look.font, new Set())
      uses.get(look.font).add(look.italic ? 'italic' : 'normal')
      text.push(shown)
      words.push({ ...look, text: shown, start: timed[index].start, end: timed[index].end, line })
    }
    return { layout: card.layout, align: card.align, tilt: card.tilt || 0, words, start: words[0].start, end: Math.max(...words.map(word => word.end)), until: 0, placed: null }
  })
  cards.forEach((card, index) => {
    const next = cards[index + 1]
    card.until = next ? Math.min(next.start, card.end + HOLD_S) : card.end + HOLD_S
  })
  return { cards, uses, text, cursor: 0 }
}

export function lyricFrameAt(styled, seconds) {
  const cards = styled.cards
  let index = styled.cursor
  if (index >= cards.length || cards[index].start > seconds) index = 0
  while (index + 1 < cards.length && cards[index + 1].start <= seconds) index++
  styled.cursor = index
  const card = cards[index]
  if (seconds < card.start || seconds >= card.until) return null
  let count = 0
  while (count < card.words.length && card.words[count].start <= seconds) count++
  return { index, count }
}

function measureRow(ctx, words, unit) {
  let x = 0
  let ascent = 0
  let descent = 0
  let previous = 0
  const items = words.map(word => {
    const px = unit * SIZE_SCALE[word.size]
    ctx.font = lyricFont(word, px)
    const metrics = ctx.measureText(word.text)
    if (previous) x += WORD_GAP * (px + previous) / 2
    const item = { word, px, x }
    x += metrics.width
    previous = px
    ascent = Math.max(ascent, metrics.actualBoundingBoxAscent)
    descent = Math.max(descent, metrics.actualBoundingBoxDescent)
    return item
  })
  return { items, width: x, ascent, descent, scale: 1 }
}

function placeCard(ctx, card, boxWidth, boxHeight) {
  const unit = boxHeight * UNIT
  const grouped = []
  for (const word of card.words) {
    const line = card.layout === 'solo' ? 0 : word.line
    if (!grouped[line]) grouped[line] = []
    grouped[line].push(word)
  }
  const rows = grouped.filter(Boolean).map(words => measureRow(ctx, words, unit))
  const stack = card.layout === 'stack'
  if (stack) {
    for (const row of rows) {
      row.scale = Math.min(boxWidth * STACK_WIDTH / row.width, boxHeight * STACK_LINE_MAX / (row.ascent + row.descent))
    }
  }
  const gap = unit * (stack ? STACK_LINE_GAP : LINE_GAP)
  let y = 0
  let blockWidth = 0
  rows.forEach((row, index) => {
    row.offset = card.layout === 'cascade' ? index * CASCADE_STEP * boxWidth : 0
    row.baseline = y + row.ascent * row.scale
    y = row.baseline + row.descent * row.scale + gap
    blockWidth = Math.max(blockWidth, row.offset + row.width * row.scale)
  })
  const blockHeight = y - gap
  const fit = Math.min(boxWidth / blockWidth, boxHeight / blockHeight, GROW[card.layout] || 1)
  const placed = []
  for (const row of rows) {
    const width = row.width * row.scale
    const align = card.layout === 'cascade' ? 0 : card.align === 'right' ? blockWidth - width : card.align === 'center' ? (blockWidth - width) / 2 : 0
    for (const item of row.items) {
      placed.push({
        text: item.word.text,
        font: lyricFont(item.word, item.px * row.scale * fit),
        x: (row.offset + align + item.x * row.scale - blockWidth / 2) * fit,
        y: (row.baseline - blockHeight / 2) * fit,
      })
    }
  }
  return placed
}

export function drawLyricFrame(ctx, styled, frame, width, height, aspect) {
  ctx.clearRect(0, 0, width, height)
  if (!frame) return
  const card = styled.cards[frame.index]
  const virtualWidth = height * aspect
  const boxWidth = virtualWidth * BOX_WIDTH
  const boxHeight = height * BOX_HEIGHT
  const key = `${boxWidth.toFixed(1)}x${boxHeight.toFixed(1)}`
  if (card.placed?.key !== key) card.placed = { key, items: placeCard(ctx, card, boxWidth, boxHeight) }
  const shift = card.align === 'left' ? -ALIGN_SHIFT : card.align === 'right' ? ALIGN_SHIFT : 0
  ctx.save()
  ctx.scale(width / virtualWidth, 1)
  ctx.translate(virtualWidth * (0.5 + shift), height / 2)
  if (card.tilt) ctx.rotate(card.tilt * Math.PI / 180)
  ctx.textAlign = 'left'
  ctx.textBaseline = 'alphabetic'
  ctx.fillStyle = 'white'
  const items = card.placed.items
  for (let index = 0; index < frame.count && index < items.length; index++) {
    const item = items[index]
    ctx.globalAlpha = index === frame.count - 1 ? 1 : PAST_ALPHA
    ctx.font = item.font
    ctx.fillText(item.text, item.x, item.y)
  }
  ctx.restore()
}
