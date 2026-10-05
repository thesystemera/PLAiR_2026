JSON.stringify((() => {
  const out = { tiles: 0, noImg: 0, imgBroken: 0, litDrawn: 0, litWaiting: 0, noCanvas: 0 }
  for (const panel of document.querySelectorAll('[data-shader-panel]')) {
    const pr = panel.getBoundingClientRect()
    if (pr.left < -10 || pr.left > 100) continue
    for (const img of panel.querySelectorAll('img')) {
      const r = img.getBoundingClientRect()
      if (r.width < 60 || r.bottom < 0 || r.top > innerHeight) continue
      out.tiles++
      if (!img.getAttribute('src')) out.noImg++
      else if (img.complete && img.naturalWidth === 0) out.imgBroken++
      const c = img.parentElement.querySelector('canvas')
      if (!c) out.noCanvas++
      else if (getComputedStyle(c).opacity === '1') out.litDrawn++
      else out.litWaiting++
    }
  }
  out.stats = window.__plairArt?.stats()
  return out
})())
