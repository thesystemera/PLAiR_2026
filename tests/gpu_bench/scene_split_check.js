// PRELOAD for pagerun.mjs (or paste into phone.mjs eval): draws the scene's main pass twice with the same uniforms,
// once with the screen split between the glass shaders and once with the full shader on every pixel, and compares
// every pixel. Result in window.results.
window.__sceneSplitCheck = async () => {
  const code = `
    const r = scene.renderer
    const gl = r.getContext()
    const w = gl.drawingBufferWidth
    const h = gl.drawingBufferHeight
    const shot = () => {
      r.render(scene.mainScene, scene.mainCamera)
      const px = new Uint8Array(w * h * 4)
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px)
      return px
    }
    const saved = { geometry: scene.glassMesh.geometry, outer: scene.glassMesh.visible, inner: scene.glassInteriorMesh.visible, button: scene.glassButtonMesh?.visible, backdrop: scene.glassBackdropMesh?.visible }
    const split = shot()
    scene.glassMesh.geometry = scene.geometry
    scene.glassMesh.visible = true
    scene.glassInteriorMesh.visible = false
    if (scene.glassButtonMesh) scene.glassButtonMesh.visible = false
    if (scene.glassBackdropMesh) scene.glassBackdropMesh.visible = false
    const whole = shot()
    scene.glassMesh.geometry = saved.geometry
    scene.glassMesh.visible = saved.outer
    scene.glassInteriorMesh.visible = saved.inner
    if (scene.glassButtonMesh) scene.glassButtonMesh.visible = saved.button
    if (scene.glassBackdropMesh) scene.glassBackdropMesh.visible = saved.backdrop
    const again = shot()
    let repeatOff = 0
    let repeatOver1 = 0
    let repeatMax = 0
    for (let i = 0; i < split.length; i += 4) {
      const d = Math.max(Math.abs(split[i] - again[i]), Math.abs(split[i + 1] - again[i + 1]), Math.abs(split[i + 2] - again[i + 2]))
      if (d > repeatMax) repeatMax = d
      if (d) repeatOff++
      if (d > 1) repeatOver1++
    }
    let max = 0
    let off = 0
    let lit = 0
    let over1 = 0
    const samples = []
    const rowsHit = new Map()
    const colsHit = new Map()
    for (let i = 0; i < split.length; i += 4) {
      const d = Math.max(Math.abs(split[i] - whole[i]), Math.abs(split[i + 1] - whole[i + 1]), Math.abs(split[i + 2] - whole[i + 2]))
      if (d > max) max = d
      if (d) off++
      if (d > 1) {
        over1++
        const x = (i / 4) % w
        const y = Math.floor(i / 4 / w)
        rowsHit.set(y, (rowsHit.get(y) || 0) + 1)
        colsHit.set(x, (colsHit.get(x) || 0) + 1)
        if (samples.length < 8) samples.push([+((x + 0.5) / w).toFixed(4), +((y + 0.5) / h).toFixed(4), split[i], split[i + 1], split[i + 2], whole[i], whole[i + 1], whole[i + 2]])
      }
      if (whole[i] + whole[i + 1] + whole[i + 2] > 0) lit++
    }
    const info = gl.getExtension('WEBGL_debug_renderer_info')
    return JSON.stringify({
      size: [w, h], pixels: w * h, litPixels: lit, differing: off, differingByMoreThan1: over1, sameSplitDrawnTwice: { differing: repeatOff, differingByMoreThan1: repeatOver1, maxDifference: repeatMax }, samples, topRows: [...rowsHit].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([y, n]) => [+((y + 0.5) / h).toFixed(4), n]), topCols: [...colsHit].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([x, n]) => [+((x + 0.5) / w).toFixed(4), n]), opaque: Array.from(scene.splitRects.subarray(0, scene.splitCount * 4)).map(v => +v.toFixed(4)), maxDifference: max,
      panelsDrawn: saved.outer || saved.inner, interior: saved.inner, interiorUnderButton: saved.button, backgroundOnly: saved.backdrop, opaqueRects: scene.splitCount, glassRects: scene.splitGlassCount,
      button: scene.splitHasButton ? Array.from(scene.splitButton) : null, build: scene.glassButtonMesh ? 'new' : 'old',
      gpu: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : null,
    })
  `
  return JSON.parse(await window.__plairScene.eval(code))
}
window.addEventListener('load', () => setTimeout(async () => {
  try {
    document.querySelector('button[aria-label="Close"]')?.click()
    await new Promise(r => setTimeout(r, 4000))
    const checks = []
    for (let i = 0; i < 3; i++) {
      checks.push(await window.__sceneSplitCheck())
      await new Promise(r => setTimeout(r, 700))
    }
    window.results = { checks, art: window.__plairArt?.stats?.() }
  } catch (error) {
    window.results = 'error: ' + (error?.message || error)
  }
}, 5000))
