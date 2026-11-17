export const backgroundVertex = (head = '', main = '') => `
  varying vec2 vUv;
  varying vec2 vFxUv;
  varying vec2 vLocal;
  varying vec2 vFallbackUv;
  varying vec2 vTextUv;
  uniform vec2 u_canvas_resolution;
  uniform vec2 u_tex_resolution;
  uniform vec2 u_parallax;
  uniform vec2 u_glitch;
  uniform float u_scale;
  uniform vec2 u_frame_offset;
  uniform float u_frame_scale;
  uniform float u_rotation;

  vec2 getCoverUV(vec2 uv, vec2 canvasRes, vec2 texRes) {
    float canvasAspect = canvasRes.x / canvasRes.y;
    float texAspect = texRes.x / texRes.y;
    if (texAspect > canvasAspect) {
      uv.x = uv.x * canvasAspect / texAspect + (1.0 - canvasAspect / texAspect) / 2.0;
    } else {
      uv.y = uv.y * texAspect / canvasAspect + (1.0 - texAspect / canvasAspect) / 2.0;
    }
    return uv;
  }
${head}
  void main() {
    vUv = uv;
    vec2 center = vec2(0.5, 0.5);
    vec2 corrected = getCoverUV(uv, u_canvas_resolution, u_tex_resolution);
    float cosRot = cos(u_rotation);
    float sinRot = sin(u_rotation);
    mat2 rotationMatrix = mat2(cosRot, -sinRot, sinRot, cosRot);
    vec2 glitchOffset = u_glitch / u_canvas_resolution;
    vec2 framed = (corrected - center) / u_frame_scale + u_frame_offset;
    vFxUv = rotationMatrix * (framed - u_parallax / u_canvas_resolution - glitchOffset) / u_scale + center;
    vLocal = rotationMatrix * (corrected - center) / (u_frame_scale * u_scale);
    vFallbackUv = rotationMatrix * framed / (u_scale + 0.2) + center;
    vTextUv = uv - glitchOffset;
${main}    gl_Position = vec4(position, 1.0);
  }
`

export const BACKGROUND_VERTEX = backgroundVertex()

export const IMAGE_BODY = `
  varying vec2 vUv;
  varying vec2 vFxUv;
  varying vec2 vLocal;
  varying vec2 vFallbackUv;
  varying vec2 vTextUv;
  uniform sampler2D u_texture;
  uniform sampler2D u_texture_prev;
  uniform sampler2D u_texture_near;
  uniform sampler2D u_texture_far;
  uniform sampler2D u_texture_prev_far;
  uniform sampler2D u_depth_map;
  uniform vec3 u_orbit;
  uniform sampler2D u_text_texture;
  uniform float u_text_empty;
  uniform float u_transition;
  uniform vec2 u_canvas_resolution;
  uniform vec2 u_glitch;
  uniform float u_time;
  uniform mat3 u_grade;
  uniform float u_grade_lift;
  uniform float u_max_blur;
  uniform float u_chromatic;
  uniform float u_flicker;
  uniform float u_has_depth_map;
  uniform float u_focal_depth;
  uniform float u_focal_range;
  uniform float u_is_capture;
  uniform vec2 u_tex_resolution;

  float focalDepth;
  float focusBase;
  float focusBlur;
  vec3 lyricInk = vec3(1.0);

  vec4 splineLevel(sampler2D tex, vec2 uv, float level) {
    vec2 size = max(vec2(1.0), floor(u_tex_resolution / exp2(level)));
    vec2 p = uv * size - 0.5;
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
    return g0.y * (g0.x * textureLod(tex, c0, level) + g1.x * textureLod(tex, vec2(c1.x, c0.y), level))
         + g1.y * (g0.x * textureLod(tex, vec2(c0.x, c1.y), level) + g1.x * textureLod(tex, c1, level));
  }

  vec4 softColor(vec2 uv, float base, float blur) {
    if (blur < 0.75) return textureLod(u_texture, uv, base + blur);
    float lod = base + blur;
    if (lod < 1.0) return mix(textureLod(u_texture, uv, 0.0), textureLod(u_texture_near, uv, 0.0), lod);
    if (lod < 2.0) return mix(textureLod(u_texture_near, uv, 0.0), textureLod(u_texture_far, uv, 0.0), lod - 1.0);
    return textureLod(u_texture_far, uv, lod - 2.0);
  }

  vec4 leavingColor(vec2 uv, float base, float blur) {
    if (blur < 0.75) return textureLod(u_texture_prev, uv, base + blur);
    return textureLod(u_texture_prev_far, uv, max(base + blur - 2.0, 0.0));
  }

  vec2 depthView(vec2 uv, vec2 local) {
    vec2 texel = uv * u_tex_resolution;
    focusBase = max(0.0, 0.5 * log2(max(dot(dFdx(texel), dFdx(texel)), dot(dFdy(texel), dFdy(texel)))));
    if (u_has_depth_map < 0.5) {
      focusBlur = u_is_capture > 0.5 ? 0.0 : u_max_blur;
      return uv;
    }
    float away = splineLevel(u_depth_map, uv, 2.0).g - focalDepth;
    focusBlur = clamp((abs(away) - u_focal_range) * u_max_blur, 0.0, 4.5);
    return uv + (u_orbit.xy + local * u_orbit.z) * away;
  }

  vec4 gradeColor(vec4 color, mat3 grade) {
    color.rgb = clamp(grade * color.rgb + u_grade_lift, 0.0, 1.0);
    return color;
  }

  vec4 chromaticImage(vec2 uv, mat3 grade) {
    vec4 color = gradeColor(softColor(uv, focusBase, focusBlur), grade);
    if (abs(u_chromatic) > 0.5 && u_is_capture < 0.5) {
      vec2 offset = vec2(u_chromatic, 0.0) / u_canvas_resolution;
      color.r = clamp(dot(vec3(grade[0].r, grade[1].r, grade[2].r), softColor(uv + offset, focusBase, focusBlur).rgb) + u_grade_lift, 0.0, 1.0);
      color.b = clamp(dot(vec3(grade[0].b, grade[1].b, grade[2].b), softColor(uv - offset, focusBase, focusBlur).rgb) + u_grade_lift, 0.0, 1.0);
    }
    if (u_transition < 1.0) color = mix(gradeColor(leavingColor(uv, focusBase, focusBlur), grade), color, u_transition * u_transition * (3.0 - 2.0 * u_transition));
    return color;
  }

  float scanline(vec2 uv, float time, float glitch) {
    if (abs(glitch) > 20.0) return sin((uv.y + time * 0.1) * u_canvas_resolution.y * 0.25) * 0.05;
    return 0.0;
  }

  vec3 fallbackGradient(vec2 uv) {
    vec3 color1 = vec3(0.545, 0.360, 0.964);
    vec3 color2 = vec3(0.231, 0.509, 0.964);
    vec3 color = mix(color1, color2, uv.y);
    return color * 0.4;
  }

  vec4 coverImage() {
    focalDepth = u_focal_depth;
    vec2 uv = depthView(vFxUv, vLocal);
    return chromaticImage(uv, u_grade);
  }

`

export const FRAMING_FUNCTIONS = `
  vec2 coverFit(vec2 uv, vec2 canvasRes) {
    float canvasAspect = canvasRes.x / canvasRes.y;
    float texAspect = u_tex_resolution.x / u_tex_resolution.y;
    if (texAspect > canvasAspect) {
      uv.x = uv.x * canvasAspect / texAspect + (1.0 - canvasAspect / texAspect) / 2.0;
    } else {
      uv.y = uv.y * texAspect / canvasAspect + (1.0 - texAspect / canvasAspect) / 2.0;
    }
    return uv;
  }

  vec2 framedUv(vec2 local, vec2 res, vec2 offset, float zoom) {
    vec2 framed = (coverFit(local, res) - 0.5) / (u_frame_scale * zoom) + u_frame_offset + offset;
    vec2 p = framed - (u_parallax + u_glitch) / u_canvas_resolution;
    return mat2(u_fx_rot.x, -u_fx_rot.y, u_fx_rot.y, u_fx_rot.x) * p / u_scale + 0.5;
  }
`

export const FRAMING = `
  uniform vec2 u_parallax;
  uniform float u_scale;
  uniform vec2 u_frame_offset;
  uniform float u_frame_scale;
  uniform vec2 u_fx_rot;
` + FRAMING_FUNCTIONS

export const finishImage = (name, ending) => `  ${name} {
    vec4 finalColor = lookImage();

    if (finalColor.a < 0.01 && u_transition < 0.01) {
       finalColor.rgb = fallbackGradient(vFallbackUv);
       finalColor.a = 1.0;
    }

    if (u_is_capture < 0.5) {
       finalColor.rgb += scanline(vUv, u_time, u_glitch.x);
    }
    finalColor.rgb *= u_flicker;

    if (u_text_empty < 0.5) {
      vec4 lyricsSample = texture2D(u_text_texture, vTextUv);
      if(lyricsSample.a > 0.01) {
        finalColor.rgb = mix(finalColor.rgb, lyricsSample.rgb * lyricInk, lyricsSample.a);
      }
    }
    ${ending}
  }
`
