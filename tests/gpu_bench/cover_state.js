JSON.stringify((() => {
  const out = { covers: 0, drawn: 0, waiting: 0, imgs: 0 }
  for (const panel of document.querySelectorAll('[data-shader-panel]')) {
    const pr = panel.getBoundingClientRect()
    if (pr.left < -10 || pr.left > 100) continue
    out.imgs += panel.querySelectorAll('img').length
    for (const c of panel.querySelectorAll('canvas[role=img]')) {
      const r = c.getBoundingClientRect()
      if (r.width < 60 || r.bottom < 0 || r.top > innerHeight) continue
      out.covers++
      if (getComputedStyle(c).opacity === '1') out.drawn++
      else out.waiting++
    }
  }
  const s = window.__plairArt?.stats()
  out.warm = s?.warm
  out.textureMB = s?.textureMB
  return out
})())
