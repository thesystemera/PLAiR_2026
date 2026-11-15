export const backgroundVertex = (head = '', main = '') => `
  varying vec2 vUv;
  varying vec2 vFxUv;
  varying vec2 vFallbackUv;
  varying vec2 vTextUv;
  varying vec2 vHue;
  uniform vec2 u_canvas_resolution;
  uniform vec2 u_tex_resolution;
  uniform vec2 u_parallax;
  uniform vec2 u_glitch;
  uniform float u_scale;
  uniform vec2 u_frame_offset;
  uniform float u_frame_scale;
  uniform float u_rotation;
  uniform float u_hue;

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
    vFallbackUv = rotationMatrix * framed / (u_scale + 0.2) + center;
    vTextUv = uv - glitchOffset;
    vHue = vec2(cos(u_hue), sin(u_hue));
${main}    gl_Position = vec4(position, 1.0);
  }
`

export const BACKGROUND_VERTEX = backgroundVertex()

export const IMAGE_BODY = `
  varying vec2 vUv;
  varying vec2 vFxUv;
  varying vec2 vFallbackUv;
  varying vec2 vTextUv;
  varying vec2 vHue;
  uniform sampler2D u_texture;
  uniform sampler2D u_texture_prev;
  uniform sampler2D u_depth_map;
  uniform sampler2D u_text_texture;
  uniform float u_text_empty;
  uniform sampler2D u_video_clip;
  uniform float u_video_clip_blend;
  uniform float u_transition;
  uniform vec2 u_canvas_resolution;
  uniform vec2 u_glitch;
  uniform float u_time;
  uniform float u_brightness;
  uniform float u_contrast;
  uniform float u_saturation;
  uniform float u_hue;
  uniform float u_max_blur;
  uniform float u_chromatic;
  uniform float u_flicker;
  uniform float u_has_depth_map;
  uniform float u_focal_depth;
  uniform float u_focal_range;
  uniform float u_is_capture;
  uniform vec2 u_tex_resolution;

  float focalDepth;

  const vec2 BOKEH_DIR_1 = vec2(1.0, 0.0);
  const vec2 BOKEH_DIR_2 = vec2(-0.5, 0.8660254);
  const vec2 BOKEH_DIR_3 = vec2(-0.5, -0.8660254);

  float getBlurAmount(vec2 uv) {
    if (u_has_depth_map < 0.5) {
       if (u_is_capture > 0.5) return 0.0;
       return u_max_blur;
    }
    return 0.0;
  }

  vec4 sampleFocus(vec2 uv, float transition) {
    vec2 texel = uv * u_tex_resolution;
    float lod = max(0.0, 0.5 * log2(max(dot(dFdx(texel), dFdx(texel)), dot(dFdy(texel), dFdy(texel)))));
    vec2 clamped = uv;
    float depth = textureLod(u_depth_map, clamped, lod + 2.5).g;
    lod += clamp((abs(depth - focalDepth) - u_focal_range) * u_max_blur, 0.0, 4.5);
    vec4 color = textureLod(u_texture, clamped, lod);
    if (transition >= 1.0) return color;
    return mix(textureLod(u_texture_prev, clamped, lod), color, transition * transition * (3.0 - 2.0 * transition));
  }

  vec4 sampleBlend(vec2 uv, float transition) {
    return mix(texture2D(u_texture_prev, uv), texture2D(u_texture, uv), transition);
  }

  vec4 sampleBokeh(vec2 uv, float blur, float transition) {
    float radius = min(blur, 60.0) * 0.0016;
    vec4 color = sampleBlend(uv + BOKEH_DIR_1 * radius, transition);
    color += sampleBlend(uv + BOKEH_DIR_2 * radius, transition);
    color += sampleBlend(uv + BOKEH_DIR_3 * radius, transition);
    return color / 3.0;
  }

  vec4 sampleBokehCurrent(vec2 uv, float blur) {
    float radius = min(blur, 60.0) * 0.0016;
    vec4 color = texture2D(u_texture, uv + BOKEH_DIR_1 * radius);
    color += texture2D(u_texture, uv + BOKEH_DIR_2 * radius);
    color += texture2D(u_texture, uv + BOKEH_DIR_3 * radius);
    return color / 3.0;
  }

  vec4 sampleImage(vec2 uv, float blurAmount, float transition) {
    if (u_has_depth_map > 0.5) return sampleFocus(uv, transition);
    vec4 color;
    float blendFactor = transition * transition * (3.0 - 2.0 * transition);
    if (blurAmount > 1.0 && u_is_capture < 0.5) {
      if (transition < 1.0) {
        color = sampleBokeh(uv, blurAmount, blendFactor);
      } else {
        color = sampleBokehCurrent(uv, blurAmount);
      }
    } else if (transition < 1.0) {
      color = sampleBlend(uv, blendFactor);
    } else {
      color = texture2D(u_texture, uv);
    }
    return color;
  }

  vec4 gradeColor(vec4 color, vec2 hue, bool rotateHue) {
    color.rgb = (color.rgb - 0.5) * u_contrast + 0.5;
    color.rgb *= u_brightness;
    float luma = dot(color.rgb, vec3(0.299, 0.587, 0.114));
    color.rgb = mix(vec3(luma), color.rgb, u_saturation);
    if (rotateHue) {
      float cosHue = hue.x;
      float sinHue = hue.y;
      color.rgb = vec3(
        dot(color.rgb, vec3(0.213, 0.715, 0.072)) + cosHue * dot(color.rgb, vec3(0.787, -0.715, -0.072)) - sinHue * dot(color.rgb, vec3(-0.213, -0.715, 0.928)),
        dot(color.rgb, vec3(0.213, 0.715, 0.072)) + cosHue * dot(color.rgb, vec3(-0.213, 0.285, -0.072)) + sinHue * dot(color.rgb, vec3(0.143, -0.285, 0.142)),
        dot(color.rgb, vec3(0.213, 0.715, 0.072)) + cosHue * dot(color.rgb, vec3(-0.213, -0.715, 0.928)) + sinHue * dot(color.rgb, vec3(-0.787, 0.715, 0.072))
      );
    }
    color.rgb = clamp(color.rgb, 0.0, 1.0);
    return color;
  }

  vec4 applyEffects(vec2 uv, float blurAmount, float transition) {
    return gradeColor(sampleImage(uv, blurAmount, transition), vHue, abs(u_hue) > 0.001);
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
    vec2 uv = vFxUv;
    float blurAmount = getBlurAmount(uv);
    vec4 finalColor;

    int chromaticTaps = (abs(u_chromatic) > 0.5 && u_is_capture < 0.5) ? 3 : 1;
    vec2 chromaticOffset = vec2(u_chromatic, 0.0) / u_canvas_resolution;
    for (int k = 0; k < chromaticTaps; k++) {
      vec2 tapUv = k == 0 ? uv : (k == 1 ? uv + chromaticOffset : uv - chromaticOffset);
      vec4 tap = applyEffects(tapUv, blurAmount, u_transition);
      if (k == 0) finalColor = tap;
      else if (k == 1) finalColor.r = tap.r;
      else finalColor.b = tap.b;
    }

    if (u_video_clip_blend > 0.01) {
      vec4 clipColor = texture2D(u_video_clip, uv);
      finalColor = mix(finalColor, clipColor, u_video_clip_blend);
    }
    return finalColor;
  }

`

export const FRAMING = `
  uniform vec2 u_parallax;
  uniform float u_scale;
  uniform vec2 u_frame_offset;
  uniform float u_frame_scale;
  uniform vec2 u_fx_rot;

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

  vec4 chromaticImage(vec2 uv, float blurAmount, vec2 hue) {
    vec4 color;
    int taps = (abs(u_chromatic) > 0.5 && u_is_capture < 0.5) ? 3 : 1;
    vec2 chromaticOffset = vec2(u_chromatic, 0.0) / u_canvas_resolution;
    for (int k = 0; k < taps; k++) {
      vec2 tapUv = k == 0 ? uv : (k == 1 ? uv + chromaticOffset : uv - chromaticOffset);
      vec4 tap = gradeColor(sampleImage(tapUv, blurAmount, u_transition), hue, true);
      if (k == 0) color = tap;
      else if (k == 1) color.r = tap.r;
      else color.b = tap.b;
    }
    return color;
  }
`

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
        finalColor.rgb = mix(finalColor.rgb, lyricsSample.rgb, lyricsSample.a);
      }
    }
    ${ending}
  }
`
