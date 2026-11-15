import {
  LinearFilter,
  Mesh,
  NearestFilter,
  OrthographicCamera,
  PlaneGeometry,
  Scene,
  ShaderMaterial,
  Vector2,
  WebGLRenderTarget,
} from 'three'

const POOL_FRAMES = 6
const READY_TIMEOUT_MS = 8000

export const NV12_COLOR_SPACE = { primaries: 'bt709', transfer: 'bt709', matrix: 'bt709', fullRange: false }

const PACK_VERTEX = `
  void main() {
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`

const PACK_FRAGMENT = `
  uniform sampler2D u_frame;
  uniform vec2 u_size;

  const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

  float lumaAt(float column, float row) {
    vec3 rgb = texelFetch(u_frame, ivec2(int(column), int(u_size.y - 1.0 - row)), 0).rgb;
    return (16.0 + 219.0 * dot(rgb, LUMA)) / 255.0;
  }

  vec2 chromaAt(float column, float row) {
    vec2 centre = vec2(2.0 * column + 1.0, u_size.y - (2.0 * row + 1.0)) / u_size;
    vec3 rgb = texture2D(u_frame, centre).rgb;
    float luma = dot(rgb, LUMA);
    return (128.0 + 224.0 * vec2((rgb.b - luma) / 1.8556, (rgb.r - luma) / 1.5748)) / 255.0;
  }

  void main() {
    float x = floor(gl_FragCoord.x);
    float y = floor(gl_FragCoord.y);
    if (y < u_size.y) {
      gl_FragColor = vec4(lumaAt(4.0 * x, y), lumaAt(4.0 * x + 1.0, y), lumaAt(4.0 * x + 2.0, y), lumaAt(4.0 * x + 3.0, y));
    } else {
      float row = y - u_size.y;
      gl_FragColor = vec4(chromaAt(2.0 * x, row), chromaAt(2.0 * x + 1.0, row));
    }
  }
`

export function frameReadbackSupported(width, height) {
  if (width % 4 !== 0 || height % 2 !== 0 || typeof VideoFrame === 'undefined') return false
  try {
    new VideoFrame(new Uint8Array(6), { format: 'NV12', codedWidth: 2, codedHeight: 2, timestamp: 0 }).close()
    return true
  } catch {
    return false
  }
}

const nextTask = () => new Promise(resolve => {
  const channel = new MessageChannel()
  channel.port1.onmessage = () => { channel.port1.close(); resolve() }
  channel.port2.postMessage(0)
})

export function createFrameReadback(renderer, width, height) {
  const gl = renderer.getContext()
  const packedWidth = width / 4
  const packedHeight = height * 1.5
  const byteLength = width * height * 1.5
  const frameTarget = new WebGLRenderTarget(width, height, { depthBuffer: false, minFilter: LinearFilter, magFilter: LinearFilter, generateMipmaps: false })
  const packedTarget = new WebGLRenderTarget(packedWidth, packedHeight, { depthBuffer: false, minFilter: NearestFilter, magFilter: NearestFilter, generateMipmaps: false })
  const material = new ShaderMaterial({
    vertexShader: PACK_VERTEX,
    fragmentShader: PACK_FRAGMENT,
    uniforms: { u_frame: { value: frameTarget.texture }, u_size: { value: new Vector2(width, height) } },
    depthTest: false,
    depthWrite: false,
  })
  const geometry = new PlaneGeometry(2, 2)
  const mesh = new Mesh(geometry, material)
  mesh.frustumCulled = false
  const scene = new Scene()
  scene.add(mesh)
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1)
  const idle = []
  const pending = []

  const createBuffer = () => {
    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, buffer)
    gl.bufferData(gl.PIXEL_PACK_BUFFER, byteLength, gl.STREAM_READ)
    gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
    return buffer
  }

  const oldestReady = () => {
    if (gl.isContextLost()) throw new Error('The video renderer lost its graphics context')
    return gl.getSyncParameter(pending[0].sync, gl.SYNC_STATUS) === gl.SIGNALED
  }

  return {
    target: frameTarget,
    byteLength,
    capacity: POOL_FRAMES,
    pending: () => pending.length,
    oldestReady,

    submit(index) {
      renderer.setRenderTarget(packedTarget)
      renderer.render(scene, camera)
      const buffer = idle.pop() || createBuffer()
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, buffer)
      gl.readPixels(0, 0, packedWidth, packedHeight, gl.RGBA, gl.UNSIGNED_BYTE, 0)
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
      const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0)
      gl.flush()
      pending.push({ buffer, sync, index })
    },

    async untilOldestReady() {
      const deadline = performance.now() + READY_TIMEOUT_MS
      while (!oldestReady()) {
        if (performance.now() > deadline) throw new Error('The video renderer stopped answering')
        await nextTask()
      }
    },

    take(bytes) {
      const { buffer, sync, index } = pending.shift()
      gl.deleteSync(sync)
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, buffer)
      gl.getBufferSubData(gl.PIXEL_PACK_BUFFER, 0, bytes)
      gl.bindBuffer(gl.PIXEL_PACK_BUFFER, null)
      idle.push(buffer)
      return index
    },

    dispose() {
      for (const { buffer, sync } of pending) {
        gl.deleteSync(sync)
        gl.deleteBuffer(buffer)
      }
      for (const buffer of idle) gl.deleteBuffer(buffer)
      pending.length = 0
      idle.length = 0
      frameTarget.dispose()
      packedTarget.dispose()
      material.dispose()
      geometry.dispose()
    },
  }
}
