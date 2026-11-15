// node readbacktest.mjs — on the phone's page: how long WebGL2 readbacks block, for the ways of doing them. Draws into a
// 180x1920 target (the share video's packed frame), reads it into a pixel-pack buffer, waits for a fence, then times
// getBufferSubData; variants change the buffer usage, the wait, and whether the buffer is reused. Nothing is shown.
import { page } from './cdp.mjs'

const p = await page()
console.log(await p.evaluate(`(async () => {
  const canvas = new OffscreenCanvas(16, 16)
  const gl = canvas.getContext('webgl2', { antialias: false, alpha: false })
  const W = 180, H = 1920, bytes = W * H * 4
  const texture = gl.createTexture()
  gl.bindTexture(gl.TEXTURE_2D, texture)
  gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, W, H)
  const framebuffer = gl.createFramebuffer()
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer)
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0)
  const out = new Uint8Array(bytes)
  const nextTask = () => new Promise(resolve => { const c = new MessageChannel(); c.port1.onmessage = () => { c.port1.close(); resolve() }; c.port2.postMessage(0) })
  const frame = () => new Promise(resolve => requestAnimationFrame(resolve))
  const draw = shade => { gl.clearColor(shade, 0.5, 1 - shade, 1); gl.clear(gl.COLOR_BUFFER_BIT) }
  const results = []
  const run = async (label, { usage = gl.STREAM_READ, extraTasks = 0, extraFrames = 0, clientWait = false, reuse = false, rounds = 12 } = {}) => {
    const times = []
    const waits = []
    let correct = 0
    let buffer = null
    for (let i = 0; i < rounds; i++) {
      const shade = (i % 10) / 10
      draw(shade)
      if (!buffer || !reuse) {
        buffer = gl.createBuffer()
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER, buffer)
        gl.bufferData(gl.PIXEL_PACK_BUFFER, bytes, usage)
      } else {
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER, buffer)
      }
      gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, 0)
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
      const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0)
      gl.flush()
      const waitStart = performance.now()
      for (;;) {
        const signaled = clientWait
          ? [gl.ALREADY_SIGNALED, gl.CONDITION_SATISFIED].includes(gl.clientWaitSync(sync, 0, 0))
          : gl.getSyncParameter(sync, gl.SYNC_STATUS) === gl.SIGNALED
        if (signaled || performance.now() - waitStart > 3000) break
        await nextTask()
      }
      waits.push(performance.now() - waitStart)
      for (let k = 0; k < extraTasks; k++) await nextTask()
      for (let k = 0; k < extraFrames; k++) await frame()
      gl.deleteSync(sync)
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, buffer)
      const t = performance.now()
      gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, out)
      times.push(performance.now() - t)
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
      if (Math.abs(out[0] - Math.round(shade * 255)) <= 1) correct++
      if (!reuse) gl.deleteBuffer(buffer)
    }
    const mid = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)].toFixed(1)
    results.push(label.padEnd(44) + ' getBufferSubData median ' + mid(times) + ' ms (max ' + Math.max(...times).toFixed(1) + '), fence wait median ' + mid(waits) + ' ms, right pixels ' + correct + '/' + rounds)
  }
  await run('STREAM_READ, new buffer each time')
  await run('STREAM_READ, same buffer reused', { reuse: true })
  await run('STATIC_READ, new buffer', { usage: gl.STATIC_READ })
  await run('DYNAMIC_READ, new buffer', { usage: gl.DYNAMIC_READ })
  await run('STREAM_READ, clientWaitSync polling', { clientWait: true })
  await run('STREAM_READ, +3 tasks after the fence', { extraTasks: 3 })
  await run('STREAM_READ, +1 frame after the fence', { extraFrames: 1 })
  await run('STREAM_READ, +3 frames after the fence', { extraFrames: 3 })
  await run('STREAM_READ, reused, +2 frames', { reuse: true, extraFrames: 2 })
  // synchronous readPixels into memory for comparison
  {
    const times = []
    for (let i = 0; i < 12; i++) { draw(i / 12); const t = performance.now(); gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, out); times.push(performance.now() - t) }
    results.push('readPixels straight into memory'.padEnd(44) + ' median ' + [...times].sort((a, b) => a - b)[6].toFixed(1) + ' ms (max ' + Math.max(...times).toFixed(1) + ')')
  }
  return results.join(String.fromCharCode(10))
})()`))
p.close()
process.exit(0)
