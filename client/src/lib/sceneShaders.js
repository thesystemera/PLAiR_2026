import { BACKGROUND_VERTEX, FRAMING, IMAGE_BODY, backgroundVertex, finishImage } from './scene/glsl/background'
import { GLASS_BODY } from './scene/glsl/glass'
import { AMBIENT_GLOW } from './scene/glsl/glow'
import { LOOKS } from './scene/looks'

const RADIO_BUTTON_DEFINE = '#define GLASS_RADIO_BUTTON\n'

const BACKGROUND_FUNCTION = `
  uniform vec3 u_underlay;

` + finishImage('vec4 computeBackground()', 'return finalColor;')

const BACKDROP_MAIN = `
  void main() {
    vec4 background = clamp(computeBackground(), 0.0, 1.0);
    gl_FragColor = vec4(applyAmbientGlow(background.rgb + (1.0 - background.a) * u_underlay, vUv, 0.0), 1.0);
  }
`

const SCENE_MAIN = `
  void main() {
    vec4 glass = clamp(computeGlass(), 0.0, 1.0);
    vec3 color;
    if (glass.a >= 0.999) {
      color = glass.rgb;
    } else {
      vec4 background = clamp(computeBackground(), 0.0, 1.0);
      float alpha = glass.a + background.a * (1.0 - glass.a);
      color = mix(background.rgb, glass.rgb, glass.a) + (1.0 - alpha) * u_underlay;
    }
    gl_FragColor = vec4(applyAmbientGlow(color, vUv, glass.a), 1.0);
  }
`

const GLASS_MAIN = `
  void main() {
    vec4 glass = clamp(computeGlass(), 0.0, 1.0);
    gl_FragColor = vec4(applyAmbientGlow(glass.rgb, vUv, glass.a), 1.0);
  }
`

export function lookShaders(id) {
  const look = LOOKS[id]
  const body = IMAGE_BODY + (look.framing ? FRAMING : '') + look.glsl
  return {
    vertex: look.vertex ? backgroundVertex(look.vertex.head, look.vertex.main) : BACKGROUND_VERTEX,
    background: body + finishImage('void main()', 'gl_FragColor = finalColor;'),
    backdrop: body + BACKGROUND_FUNCTION + AMBIENT_GLOW + BACKDROP_MAIN,
    scene: RADIO_BUTTON_DEFINE + body + GLASS_BODY + BACKGROUND_FUNCTION + AMBIENT_GLOW + SCENE_MAIN,
  }
}

const cover = lookShaders('cover')
const backgroundVertexShader = BACKGROUND_VERTEX
const backgroundFragmentShader = cover.background
const sceneFragmentShader = cover.scene
const backdropFragmentShader = cover.backdrop
const sceneGlassFragmentShader = IMAGE_BODY + GLASS_BODY + AMBIENT_GLOW + GLASS_MAIN
const sceneGlassButtonFragmentShader = RADIO_BUTTON_DEFINE + sceneGlassFragmentShader

export { backgroundVertexShader, backgroundFragmentShader, sceneFragmentShader, sceneGlassFragmentShader, sceneGlassButtonFragmentShader, backdropFragmentShader }
