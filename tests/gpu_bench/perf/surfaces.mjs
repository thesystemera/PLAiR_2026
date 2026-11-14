// node surfaces.mjs [panel] — on-screen elements whose CSS can make Chrome draw them through an off-screen texture
// (a render pass): backdrop-filter, filter, mask, clip-path, blend mode, opacity below 1, will-change, and rounded
// clips that hold a canvas. Largest first. Changes nothing.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2]
const p = await page()
if (panel) {
  await openPanel(p, panel)
  await sleep(2000)
}
const rows = await p.evaluate(`(() => {
  const out = []
  const dpr = devicePixelRatio
  for (const el of document.querySelectorAll('body *')) {
    const box = el.getBoundingClientRect()
    if (box.width < 4 || box.height < 4 || box.bottom < 0 || box.top > innerHeight || box.right < 0 || box.left > innerWidth) continue
    const s = getComputedStyle(el)
    const why = []
    if (s.backdropFilter && s.backdropFilter !== 'none') why.push('backdrop-filter ' + s.backdropFilter)
    if (s.filter && s.filter !== 'none') why.push('filter ' + s.filter)
    const mask = s.maskImage || s.webkitMaskImage
    if (mask && mask !== 'none') why.push('mask')
    if (s.clipPath && s.clipPath !== 'none') why.push('clip-path')
    if (s.mixBlendMode && s.mixBlendMode !== 'normal') why.push('blend ' + s.mixBlendMode)
    if (+s.opacity < 1 && +s.opacity > 0) why.push('opacity ' + s.opacity)
    if (s.willChange && s.willChange !== 'auto') why.push('will-change ' + s.willChange)
    if (s.overflow !== 'visible' && parseFloat(s.borderTopLeftRadius) > 0 && el.querySelector('canvas')) why.push('rounded clip over a canvas (' + s.borderTopLeftRadius + ')')
    if (!why.length) continue
    out.push({ px: Math.round(box.width * dpr) + 'x' + Math.round(box.height * dpr), area: box.width * box.height, tag: el.tagName.toLowerCase(), cls: String(el.className?.baseVal ?? el.className).slice(0, 90), why: why.join('; ') })
  }
  out.sort((a, b) => b.area - a.area)
  return JSON.stringify(out.slice(0, 45))
})()`)
for (const row of JSON.parse(rows)) console.log(row.px.padEnd(10), row.why.padEnd(48).slice(0, 70), '|', row.tag, row.cls)
p.close()
process.exit(0)
