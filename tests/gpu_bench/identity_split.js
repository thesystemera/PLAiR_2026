const s = scene
s.bench.skip = true
await new Promise(r => setTimeout(r, 150))
const gl = s.gl
const r = s.renderer
const w = gl.drawingBufferWidth
const h = gl.drawingBufferHeight
const read = () => { const px = new Uint8Array(w * h * 4); gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px); return px }
const results = []
for (const setup of [{}, { u_chromatic: 4, u_max_blur: 8, u_transition: 0.4 }, { u_radio_button_hover: 1, u_voice_level: 0.8, u_glow_active: 1 }]) {
  const u = s.fgMaterial.uniforms
  const saved = {}
  for (const k in setup) { saved[k] = u[k].value; u[k].value = setup[k] }
  r.setRenderTarget(null)
  r.render(s.mainScene, s.mainCamera)
  const a = read()
  const geometry = s.glassMesh.geometry
  const interior = s.glassInteriorMesh.visible
  s.glassMesh.geometry = s.geometry
  s.glassMesh.visible = true
  s.glassInteriorMesh.visible = false
  r.render(s.mainScene, s.mainCamera)
  const b = read()
  s.glassMesh.geometry = geometry
  s.glassInteriorMesh.visible = interior
  let diff = 0, maxd = 0
  for (let i = 0; i < a.length; i++) { const d = Math.abs(a[i] - b[i]); if (d) { diff++; if (d > maxd) maxd = d } }
  results.push({ setup: Object.keys(setup).join('+') || 'live', w, h, interior, quads: s.splitCount, differingBytes: diff, maxDiff: maxd })
  for (const k in saved) u[k].value = saved[k]
}
s.bench.skip = false
return JSON.stringify(results)
