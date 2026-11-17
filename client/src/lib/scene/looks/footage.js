import { Vector2, Vector3, Vector4 } from 'three'
import { FOOTAGE_SIGNATURE } from '../glsl/footage'

export default {
  id: 'footage',
  framing: true,
  footage: true,
  glsl: `
  vec4 lookImage() {
    vec2 screen = clipShape(vUv);
    vec4 color = footageTreat(footageRead(u_clip0, framedClipUv(screen, u_canvas_resolution, vec2(0.0), 1.0, u_clip_res[0], u_ft_shape.z, u_ft_mix.z)), u_grade, vUv);
    if (u_ft_mix.y > 0.001) {
      vec3 other = u_ft_mix.x > 1.5
        ? footageTreat(footageRead(u_clip1, framedClipUv(vec2(1.0) - screen, u_canvas_resolution, vec2(0.0), 1.15, u_clip_res[1], -u_ft_shape.z, u_ft_mix.z)), u_grade, vUv).rgb
        : coverImage().rgb;
      if (u_ft_mix.x > 0.5 && u_ft_mix.x < 1.5) {
        float key = smoothstep(0.18, 0.55, dot(color.rgb, vec3(0.299, 0.587, 0.114)));
        color.rgb = mix(other, color.rgb, mix(1.0, key, u_ft_mix.y));
      } else {
        color.rgb = mix(color.rgb, 1.0 - (1.0 - color.rgb) * (1.0 - other), u_ft_mix.y);
      }
    }
    return color;
  }
`,
  uniforms: () => ({
    u_clip0: { value: null },
    u_clip1: { value: null },
    u_clip2: { value: null },
    u_clip3: { value: null },
    u_clip_res: { value: Array.from({ length: 4 }, () => new Vector2(16, 9)) },
    u_ft: { value: new Vector4(0, 0, 0, 1) },
    u_ft_shape: { value: new Vector4(0, 0, 0, 0) },
    u_ft_mix: { value: new Vector4(0, 0, 0, 0) },
    u_ft_dark: { value: new Vector3(0.05, 0.04, 0.08) },
    u_ft_light: { value: new Vector3(0.75, 0.7, 0.85) },
  }),
  signature: FOOTAGE_SIGNATURE,
  variant: () => ({}),
  frame: () => {},
}
