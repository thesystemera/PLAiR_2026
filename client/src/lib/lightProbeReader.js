import { PROBE_GRID, publishLightProbe } from './lightProbe'

export class LightProbeReader {
  constructor(gl) {
    this.gl = gl
    this.buffer = new Uint8Array(PROBE_GRID * PROBE_GRID * 4)
    this.pbo = null
    this.sync = null
    this.reset = () => {
      this.pbo = null
      this.sync = null
    }
    gl.canvas.addEventListener('webglcontextlost', this.reset)
  }

  get ready() {
    return !this.sync
  }

  request() {
    const gl = this.gl
    if (!this.pbo) {
      this.pbo = gl.createBuffer()
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, this.pbo)
      gl.bufferData(gl.PIXEL_PACK_BUFFER, this.buffer.byteLength, gl.STREAM_READ)
    } else {
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, this.pbo)
    }
    gl.readPixels(0, 0, PROBE_GRID, PROBE_GRID, gl.RGBA, gl.UNSIGNED_BYTE, 0)
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
    this.sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0)
  }

  collect() {
    const gl = this.gl
    if (!this.sync || gl.getSyncParameter(this.sync, gl.SYNC_STATUS) !== gl.SIGNALED) return
    gl.deleteSync(this.sync)
    this.sync = null
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, this.pbo)
    gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, this.buffer)
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
    publishLightProbe(this.buffer)
  }

  dispose() {
    this.gl.canvas.removeEventListener('webglcontextlost', this.reset)
  }
}
