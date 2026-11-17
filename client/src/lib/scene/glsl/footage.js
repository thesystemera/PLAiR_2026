export const CLIP_UNIFORMS = ['u_clip0', 'u_clip1', 'u_clip2', 'u_clip3']

export const FOOTAGE_FRAMING = `
  vec2 clipFit(vec2 uv, vec2 canvasRes, vec2 clipRes, float pan, float stack) {
    float canvasAspect = canvasRes.x / canvasRes.y;
    float clipAspect = clipRes.x / clipRes.y;
    if (stack > 0.5) {
      if (clipAspect > canvasAspect) uv.y = (uv.y - 0.5) * clipAspect / canvasAspect + 0.5 + pan;
      else uv.x = (uv.x - 0.5) * canvasAspect / clipAspect + 0.5 + pan;
    } else if (clipAspect > canvasAspect) {
      float k = canvasAspect / clipAspect;
      uv.x = uv.x * k + (1.0 - k) * 0.5 * (1.0 + pan);
    } else {
      float k = clipAspect / canvasAspect;
      uv.y = uv.y * k + (1.0 - k) * 0.5 * (1.0 + pan);
    }
    return uv;
  }

  vec2 framedClipUv(vec2 local, vec2 res, vec2 offset, float zoom, vec2 clipRes, float pan, float stack) {
    vec2 framed = (clipFit(local, res, clipRes, pan, stack) - 0.5) / (u_frame_scale * zoom) + u_frame_offset * 0.5 + offset;
    vec2 p = framed - (u_parallax + u_glitch) / u_canvas_resolution;
    return mat2(u_fx_rot.x, -u_fx_rot.y, u_fx_rot.y, u_fx_rot.x) * p / u_scale + 0.5;
  }
`

const EXPOSURE = { target: 0.5, min: 1, max: 2.6 }

export const FOOTAGE_BODY = `
  uniform sampler2D u_clip0;
  uniform sampler2D u_clip1;
  uniform sampler2D u_clip2;
  uniform sampler2D u_clip3;
  uniform vec2 u_clip_res[4];
  uniform vec4 u_ft;
  uniform vec4 u_ft_shape;
  uniform vec4 u_ft_mix;
  uniform vec3 u_ft_dark;
  uniform vec3 u_ft_light;

  vec3 footageRead(sampler2D clip, vec2 uv) {
    vec3 color = textureLod(clip, uv, 0.0).rgb;
    if (abs(u_chromatic) > 0.5 && u_is_capture < 0.5) {
      vec2 offset = vec2(u_chromatic, 0.0) / u_canvas_resolution;
      color.r = textureLod(clip, uv + offset, 0.0).r;
      color.b = textureLod(clip, uv - offset, 0.0).b;
    }
    float mean = dot(textureLod(clip, vec2(0.5), 12.0).rgb, vec3(0.299, 0.587, 0.114));
    return color * clamp(${EXPOSURE.target.toFixed(2)} / max(mean, 0.01), ${EXPOSURE.min.toFixed(2)}, ${EXPOSURE.max.toFixed(2)});
  }

  vec3 clipRead(float slot, vec2 uv) {
    if (slot < 0.5) return footageRead(u_clip0, uv);
    if (slot < 1.5) return footageRead(u_clip1, uv);
    if (slot < 2.5) return footageRead(u_clip2, uv);
    return footageRead(u_clip3, uv);
  }

  float grainAt(vec2 screen) {
    vec2 p = floor(screen * u_canvas_resolution) + floor(u_time * 24.0) * vec2(5.588238, 3.1767);
    return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))) - 0.5;
  }

  vec4 footageTreat(vec3 color, mat3 grade, vec2 screen) {
    color = mix(color, color * color * (3.0 - 2.0 * color), u_ft.y);
    float luma = dot(color, vec3(0.299, 0.587, 0.114));
    color = mix(color, mix(u_ft_dark, u_ft_light, luma), u_ft.x);
    color = clamp(grade * color + u_grade_lift, 0.0, 1.0);
    if (u_is_capture < 0.5) color += grainAt(screen) * u_ft.z * 0.2;
    return vec4(clamp(color * u_ft.w, 0.0, 1.0), 1.0);
  }

  vec2 clipShape(vec2 uv) {
    if (u_ft_shape.y > 0.5) {
      float aspect = u_canvas_resolution.x / u_canvas_resolution.y;
      vec2 p = (uv - 0.5) * vec2(aspect, 1.0);
      float segment = 6.2831853 / u_ft_shape.y;
      float angle = mod(atan(p.y, p.x) + u_ft_shape.w, segment);
      angle = min(angle, segment - angle);
      uv = 0.5 + length(p) * vec2(cos(angle) / aspect, sin(angle));
    }
    if (u_ft_shape.x > 0.5) uv.x = 0.5 - abs(uv.x - 0.5);
    return uv;
  }
`

export const FOOTAGE_SIGNATURE = ['u_clip_res', 'u_ft', 'u_ft_shape', 'u_ft_mix', 'u_ft_dark', 'u_ft_light']
