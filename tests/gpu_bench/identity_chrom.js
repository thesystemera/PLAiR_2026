// node phone.mjs eval "$(cat identity_chrom.js)": renders one frozen frame with the old three-copy chromatic shader and the current loop version, compares every pixel
(async () => {
  const b = window.__plairScene
  const { gl, scene, camera } = b.three
  b.skip = true
  await new Promise(r => setTimeout(r, 100))
  const fg = b.fg
  const current = fg.fragmentShader
  const newBlock = `    int chromaticTaps = (abs(u_chromatic) > 0.5 && u_is_capture < 0.5) ? 3 : 1;
    vec2 chromaticOffset = vec2(u_chromatic, 0.0) / u_canvas_resolution;
    for (int k = 0; k < chromaticTaps; k++) {
      vec2 tapUv = k == 0 ? uv : (k == 1 ? uv + chromaticOffset : uv - chromaticOffset);
      vec4 tap = applyEffects(tapUv, blurAmount, u_transition);
      if (k == 0) finalColor = tap;
      else if (k == 1) finalColor.r = tap.r;
      else finalColor.b = tap.b;
    }`
  const oldBlock = `    if (abs(u_chromatic) > 0.5 && u_is_capture < 0.5) {
      vec2 chromaticOffset = vec2(u_chromatic, 0.0) / u_canvas_resolution;
      vec4 colorR = applyEffects(uv + chromaticOffset, blurAmount, u_transition);
      vec4 colorG = applyEffects(uv, blurAmount, u_transition);
      vec4 colorB = applyEffects(uv - chromaticOffset, blurAmount, u_transition);
      finalColor = vec4(colorR.r, colorG.g, colorB.b, colorG.a);
    } else {
      finalColor = applyEffects(uv, blurAmount, u_transition);
    }`
  if (!current.includes(newBlock)) return 'new block not found (old build loaded?)'
  const old = current.split(newBlock).join(oldBlock)
  const ctx = gl.getContext()
  const w = ctx.drawingBufferWidth, h = ctx.drawingBufferHeight
  const read = () => { const px = new Uint8Array(w * h * 4); ctx.readPixels(0, 0, w, h, ctx.RGBA, ctx.UNSIGNED_BYTE, px); return px }
  const shot = src => { fg.fragmentShader = src; fg.needsUpdate = true; gl.setRenderTarget(null); gl.render(scene, camera); return read() }
  const results = []
  const setups = [{}, { u_chromatic: 3 }, { u_chromatic: -6, u_max_blur: 8, u_transition: 0.4 }, { u_chromatic: 4, u_panel_count: 0 }]
  for (const setup of setups) {
    const saved = {}
    for (const k in setup) { const u = fg.uniforms[k]; saved[k] = u.value; u.value = setup[k] }
    const a = shot(old), c = shot(current)
    let diff = 0, maxd = 0, where = []
    for (let i = 0; i < a.length; i++) { const d = Math.abs(a[i] - c[i]); if (d) { diff++; if (d > maxd) maxd = d; if (where.length < 6 && d > 20) where.push([(i >> 2) % w, Math.floor((i >> 2) / w), i & 3, a[i], c[i]]) } }
    results.push({ setup: Object.keys(setup).map(k => k + '=' + setup[k]).join(' ') || 'live values', differingBytes: diff, maxDiff: maxd, where })
    for (const k in saved) fg.uniforms[k].value = saved[k]
  }
  fg.fragmentShader = current; fg.needsUpdate = true
  b.skip = false
  return JSON.stringify({ w, h, results })
})()
