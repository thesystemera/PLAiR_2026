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

const MAX_BATCH_FRAMES = 8

export const NV12_COLOR_SPACE = { primaries: 'bt709', transfer: 'bt709', matrix: 'bt709', fullRange: false }

const PACK_VERTEX = `
  void main() {
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`

const PACK_FRAGMENT = `
  uniform sampler2D u_frame;
  uniform vec2 u_size;
  uniform float u_first_row;

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
    float y = floor(gl_FragCoord.y) - u_first_row;
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

export function createFrameReadback(renderer, width, height, batchLimit = MAX_BATCH_FRAMES) {
  const gl = renderer.getContext()
  const packedWidth = width / 4
  const frameRows = height * 1.5
  const frameBytes = width * height * 1.5
  const batch = Math.max(1, Math.min(batchLimit, Math.floor(gl.getParameter(gl.MAX_TEXTURE_SIZE) / frameRows)))
  const frameTarget = new WebGLRenderTarget(width, height, { depthBuffer: false, minFilter: LinearFilter, magFilter: LinearFilter, generateMipmaps: false })
  const packedTarget = new WebGLRenderTarget(packedWidth, frameRows * batch, { depthBuffer: false, minFilter: NearestFilter, magFilter: NearestFilter, generateMipmaps: false })
  packedTarget.scissorTest = true
  const material = new ShaderMaterial({
    vertexShader: PACK_VERTEX,
    fragmentShader: PACK_FRAGMENT,
    uniforms: { u_frame: { value: frameTarget.texture }, u_size: { value: new Vector2(width, height) }, u_first_row: { value: 0 } },
    depthTest: false,
    depthWrite: false,
  })
  const geometry = new PlaneGeometry(2, 2)
  const mesh = new Mesh(geometry, material)
  mesh.frustumCulled = false
  const scene = new Scene()
  scene.add(mesh)
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1)
  const bytes = new Uint8Array(frameBytes * batch)
  const waiting = []

  return {
    target: frameTarget,
    batch,
    waiting: () => waiting.length,
    full: () => waiting.length >= batch,

    submit(index) {
      const firstRow = waiting.length * frameRows
      material.uniforms.u_first_row.value = firstRow
      packedTarget.viewport.set(0, firstRow, packedWidth, frameRows)
      packedTarget.scissor.set(0, firstRow, packedWidth, frameRows)
      renderer.setRenderTarget(packedTarget)
      renderer.render(scene, camera)
      waiting.push(index)
    },

    read() {
      renderer.setRenderTarget(packedTarget)
      gl.readPixels(0, 0, packedWidth, frameRows * waiting.length, gl.RGBA, gl.UNSIGNED_BYTE, bytes)
      if (gl.isContextLost()) throw new Error('The video renderer lost its graphics context')
      const frames = waiting.map((index, slot) => ({ index, bytes: bytes.subarray(slot * frameBytes, (slot + 1) * frameBytes) }))
      waiting.length = 0
      return frames
    },

    dispose() {
      waiting.length = 0
      frameTarget.dispose()
      packedTarget.dispose()
      material.dispose()
      geometry.dispose()
    },
  }
}
