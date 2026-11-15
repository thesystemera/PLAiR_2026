export const AMBIENT_GLOW = `
  uniform vec2 u_glow_center;
  uniform vec2 u_glow_scale;
  uniform vec3 u_voice_glow;
  uniform vec3 u_on_air_glow;
  uniform float u_glow_active;

  vec3 applyAmbientGlow(vec3 color, vec2 screenUV, float glassAlpha) {
    if (u_glow_active < 0.5) return color;
    vec2 d = (screenUV - u_glow_center) * u_glow_scale;
    vec2 e = abs(screenUV - 0.5);
    float edge = smoothstep(0.225, 0.5, max(e.x, e.y));
    vec3 glow = (u_on_air_glow * (0.06 + 0.2 * edge) + u_voice_glow * exp2(-dot(d, d))) * (1.0 - 0.3 * glassAlpha);
    return color + glow * (1.0 - color);
  }
`
