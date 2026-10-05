(async () => {
  const counts = new Map()
  const protos = [WebGL2RenderingContext.prototype, WebGLRenderingContext.prototype]
  const saved = []
  for (const proto of protos) for (const fn of ['drawArrays', 'drawElements', 'clear', 'texImage2D', 'texSubImage2D', 'generateMipmap', 'readPixels', 'blitFramebuffer', 'copyTexSubImage2D']) {
    const orig = proto[fn]; if (!orig) continue
    saved.push([proto, fn, orig])
    proto[fn] = function (...a) {
      const c = this.canvas
      const key = (c.dataset?.bench || (c.dataset && (c.dataset.bench = (c.className || c.tagName || 'offscreen').toString().slice(0, 40) + '#' + Math.random().toString(36).slice(2, 5)))) || 'offscreen'
      let e = counts.get(key); if (!e) counts.set(key, e = { size: `${c.width}x${c.height}`, inDom: !!c.isConnected })
      e[fn] = (e[fn] || 0) + 1
      if (fn.startsWith('tex') && a.length >= 6 && typeof a[3] === 'number' && typeof a[4] === 'number') e.texPx = (e.texPx || 0) + a[3] * a[4]
      if (fn.startsWith('tex') && a.length === 6 && a[5] && a[5].width) e.texPx = (e.texPx || 0) + a[5].width * a[5].height
      return orig.apply(this, a)
    }
  }
  await new Promise(r => setTimeout(r, 3000))
  for (const [proto, fn, orig] of saved) proto[fn] = orig
  const out = {}
  for (const [k, v] of counts) { const o = { ...v }; for (const f in o) if (typeof o[f] === 'number') o[f] = Math.round(o[f] / 3); out[k] = o }
  return JSON.stringify(out, null, 1)
})()
