import { LinearFilter, LinearMipmapLinearFilter, Mesh, MirroredRepeatWrapping, OrthographicCamera, PlaneGeometry, Scene, ShaderMaterial, Vector2, WebGLRenderTarget } from 'three'

const NEAR_LEVEL = 1
export const FAR_LEVEL = 2

const VERTEX = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`

const FRAGMENT = `
  varying vec2 vUv;
  uniform sampler2D u_source;
  uniform vec2 u_size;
  uniform float u_level;

  void main() {
    vec2 size = max(vec2(1.0), floor(u_size / exp2(u_level)));
    vec2 p = vUv * size - 0.5;
    vec2 f = fract(p);
    vec2 i = p - f;
    vec2 f2 = f * f;
    vec2 f3 = f2 * f;
    vec2 w0 = (1.0 - 3.0 * f + 3.0 * f2 - f3) / 6.0;
    vec2 w1 = (4.0 - 6.0 * f2 + 3.0 * f3) / 6.0;
    vec2 g0 = w0 + w1;
    vec2 g1 = 1.0 - g0;
    vec2 c0 = (i + 0.5 - 1.0 + w1 / g0) / size;
    vec2 c1 = (i + 0.5 + 1.0 + (f3 / 6.0) / g1) / size;
    gl_FragColor = g0.y * (g0.x * textureLod(u_source, c0, u_level) + g1.x * textureLod(u_source, vec2(c1.x, c0.y), u_level))
                 + g1.y * (g0.x * textureLod(u_source, vec2(c0.x, c1.y), u_level) + g1.x * textureLod(u_source, c1, u_level));
  }
`

export function createSoftener(renderer) {
  const material = new ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    uniforms: { u_source: { value: null }, u_size: { value: new Vector2(1, 1) }, u_level: { value: 1 } },
    depthTest: false,
    depthWrite: false,
  })
  const geometry = new PlaneGeometry(2, 2)
  const mesh = new Mesh(geometry, material)
  mesh.frustumCulled = false
  const scene = new Scene()
  scene.add(mesh)
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1)

  const target = (width, height, mipmaps) => {
    const made = new WebGLRenderTarget(width, height, {
      minFilter: mipmaps ? LinearMipmapLinearFilter : LinearFilter,
      magFilter: LinearFilter,
      wrapS: MirroredRepeatWrapping,
      wrapT: MirroredRepeatWrapping,
      generateMipmaps: false,
      depthBuffer: false,
      stencilBuffer: false,
    })
    if (mipmaps) made.texture.mipmaps = Array.from({ length: Math.floor(Math.log2(Math.max(width, height))) + 1 }, () => ({}))
    return made
  }

  const draw = (made, level, source, sourceLevel) => {
    const width = Math.max(1, made.width >> level)
    const height = Math.max(1, made.height >> level)
    made.viewport.set(0, 0, width, height)
    made.scissor.set(0, 0, width, height)
    material.uniforms.u_source.value = source
    material.uniforms.u_size.value.set(source.image.width, source.image.height)
    material.uniforms.u_level.value = sourceLevel
    renderer.setRenderTarget(made, 0, level)
    renderer.render(scene, camera)
  }

  return {
    soften(source) {
      const { width, height } = source.image
      const near = target(width, height, false)
      draw(near, 0, source, NEAR_LEVEL)
      const far = target(width, height, true)
      for (let level = 0; level < far.texture.mipmaps.length; level++) draw(far, level, source, level + FAR_LEVEL)
      renderer.setRenderTarget(null)
      return { near, far, dispose: () => { near.dispose(); far.dispose() } }
    },
    dispose() {
      material.dispose()
      geometry.dispose()
    },
  }
}
