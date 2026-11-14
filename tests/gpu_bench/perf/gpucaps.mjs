// node gpucaps.mjs — what the phone's GPU offers the cover renderer: the float render-target extensions and whether
// the coarse path's four render targets make a complete framebuffer. Uses a throwaway 16x16 context and releases it.
import { page } from './cdp.mjs'

const p = await page()
console.log(await p.evaluate(`(() => {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 16
  const gl = canvas.getContext('webgl2')
  if (!gl) return 'no webgl2'
  const info = gl.getExtension('WEBGL_debug_renderer_info')
  const out = {
    renderer: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : null,
    colorBufferFloat: !!gl.getExtension('EXT_color_buffer_float'),
    colorBufferHalfFloat: !!gl.getExtension('EXT_color_buffer_half_float'),
    floatLinear: !!gl.getExtension('OES_texture_float_linear'),
    maxDrawBuffers: gl.getParameter(gl.MAX_DRAW_BUFFERS),
    maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
  }
  const formats = [[gl.RG16F, gl.RG, gl.HALF_FLOAT], [gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT], [gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT], [gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE]]
  const textures = formats.map(([internal, format, type]) => {
    const texture = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, 64, 64, 0, format, type, null)
    return texture
  })
  const framebuffer = gl.createFramebuffer()
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer)
  textures.forEach((texture, index) => gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + index, gl.TEXTURE_2D, texture, 0))
  gl.drawBuffers(textures.map((_, index) => gl.COLOR_ATTACHMENT0 + index))
  out.coarseTargetsComplete = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE
  gl.getExtension('WEBGL_lose_context')?.loseContext()
  return JSON.stringify(out)
})()`))
p.close()
process.exit(0)
