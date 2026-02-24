import {memo, useEffect, useMemo, useRef, useState} from 'react'
import {Canvas, useFrame} from '@react-three/fiber'
import {
    CanvasTexture,
    ClampToEdgeWrapping,
    DataTexture,
    LinearFilter,
    MathUtils,
    Mesh,
    OrthographicCamera,
    PlaneGeometry,
    RepeatWrapping,
    RGBAFormat,
    Scene,
    ShaderMaterial,
    Vector2,
    Vector3,
    Vector4,
    VideoTexture,
    WebGLRenderTarget,
} from 'three'
import {TextRenderer} from '../lib/textRenderer'
import {useUIState, useVideoClips} from '../contexts/UIStateContext'
import {PANEL, useDynamicTheme} from '../contexts/DynamicThemeContext'



const backgroundVertexShader = `
  varying vec2 vUv;
  varying vec2 vUvCorrected;
  uniform vec2 u_canvas_resolution;
  uniform vec2 u_tex_resolution;

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

  void main() {
    vUv = uv;
    vUvCorrected = getCoverUV(uv, u_canvas_resolution, u_tex_resolution);
    gl_Position = vec4(position, 1.0);
  }
`

const backgroundFragmentShader = `
  varying vec2 vUv;
  varying vec2 vUvCorrected;
  uniform sampler2D u_texture;
  uniform sampler2D u_texture_prev;
  uniform sampler2D u_depth_map;
  uniform sampler2D u_text_texture;
  uniform sampler2D u_video_clip;
  uniform float u_video_clip_blend;
  uniform float u_transition;
  uniform vec2 u_tex_resolution;
  uniform vec2 u_canvas_resolution;
  uniform vec2 u_parallax;
  uniform vec2 u_glitch;
  uniform float u_time;
  uniform float u_scale;
  uniform vec2 u_frame_offset;
  uniform float u_frame_scale;
  uniform float u_rotation;
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

  float getBlurAmount(vec2 uv) {
    if (u_has_depth_map < 0.5) {
       if (u_is_capture > 0.5) return 0.0;
       return u_max_blur; 
    }
    
    vec2 clampedUV = clamp(uv, 0.0, 1.0);
    float depth = texture2D(u_depth_map, clampedUV).r;
    float blurAmount = abs(depth - u_focal_depth) - u_focal_range;
    return max(0.0, blurAmount) * u_max_blur * 2.0 + (u_max_blur * 0.05);
  }

  vec4 sampleBokeh(sampler2D tex, vec2 uv, vec2 texelSize, float blur, float transition) {
    if (u_is_capture > 0.5) {
       vec4 texCurrent = texture2D(u_texture, uv);
       vec4 texPrev = texture2D(u_texture_prev, uv);
       return mix(texPrev, texCurrent, transition);
    }
    float cappedBlur = min(blur, 60.0);
    vec4 color = vec4(0.0);
    float total = 0.0;
    float radius = cappedBlur * 0.008;
    for (float ang = 0.0; ang < 6.2831853; ang += 2.0943951) {
      for (float dist = 0.2; dist < 1.0; dist += 0.8) {
        float r = dist * radius;
        vec2 offset = vec2(cos(ang), sin(ang)) * r;
        vec2 sampleUV = clamp(uv + offset, 0.0, 1.0);
        vec4 texCurrent = texture2D(u_texture, sampleUV);
        vec4 texPrev = texture2D(u_texture_prev, sampleUV);
        color += mix(texPrev, texCurrent, transition);
        total += 1.0;
      }
    }
    return color / total;
  }

  vec4 applyEffects(sampler2D tex, vec2 uv, vec2 texelSize, float blurAmount, float transition) {
    vec4 color;
    float blendFactor = transition * transition * (3.0 - 2.0 * transition);
    if (blurAmount > 1.0 && u_is_capture < 0.5) {
      color = sampleBokeh(tex, uv, texelSize, blurAmount, blendFactor);
    } else {
      if (transition < 1.0) {
        vec4 texCurrent = texture2D(u_texture, uv);
        vec4 texPrev = texture2D(u_texture_prev, uv);
        color = mix(texPrev, texCurrent, blendFactor);
      } else {
        color = texture2D(u_texture, uv);
      }
    }
    color.rgb = (color.rgb - 0.5) * u_contrast + 0.5;
    color.rgb *= u_brightness;
    float luma = dot(color.rgb, vec3(0.299, 0.587, 0.114));
    color.rgb = mix(vec3(luma), color.rgb, u_saturation);
    if (abs(u_hue) > 0.001) {
      float cosHue = cos(u_hue);
      float sinHue = sin(u_hue);
      color.rgb = vec3(
        dot(color.rgb, vec3(0.213, 0.715, 0.072)) + cosHue * dot(color.rgb, vec3(0.787, -0.715, -0.072)) - sinHue * dot(color.rgb, vec3(-0.213, -0.715, 0.928)),
        dot(color.rgb, vec3(0.213, 0.715, 0.072)) + cosHue * dot(color.rgb, vec3(-0.213, 0.285, -0.072)) + sinHue * dot(color.rgb, vec3(0.143, -0.285, 0.142)),
        dot(color.rgb, vec3(0.213, 0.715, 0.072)) + cosHue * dot(color.rgb, vec3(-0.213, -0.715, 0.928)) + sinHue * dot(color.rgb, vec3(-0.787, 0.715, 0.072))
      );
    }
    color.rgb = clamp(color.rgb, 0.0, 1.0);
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

  void main() {
    vec2 canvasRes = u_canvas_resolution;
    vec2 texRes = u_tex_resolution;
    vec2 texelSize = 1.0 / texRes;
    vec2 center = vec2(0.5, 0.5);
    vec2 screenUV = vUv;

    float cosRot = cos(u_rotation);
    float sinRot = sin(u_rotation);
    mat2 rotationMatrix = mat2(cosRot, -sinRot, sinRot, cosRot);

    vec2 uv = vUvCorrected;
    uv -= center;
    uv /= u_frame_scale;
    uv += u_frame_offset;
    uv += center;
    uv -= u_parallax / canvasRes;
    uv -= u_glitch / canvasRes;
    uv -= center;
    uv = rotationMatrix * uv;
    uv /= u_scale;
    uv += center;

    float blurAmount = getBlurAmount(uv);
    vec4 finalColor;

    if (abs(u_chromatic) > 0.5 && u_is_capture < 0.5) {
      vec2 chromaticOffset = vec2(u_chromatic, 0.0) / canvasRes;
      vec4 colorR = applyEffects(u_texture, uv + chromaticOffset, texelSize, blurAmount, u_transition);
      vec4 colorG = applyEffects(u_texture, uv, texelSize, blurAmount, u_transition);
      vec4 colorB = applyEffects(u_texture, uv - chromaticOffset, texelSize, blurAmount, u_transition);
      finalColor = vec4(colorR.r, colorG.g, colorB.b, colorG.a);
    } else {
      finalColor = applyEffects(u_texture, uv, texelSize, blurAmount, u_transition);
    }

    // Blend in video clip if active (same UV, gets same effects)
    if (u_video_clip_blend > 0.01) {
      vec4 clipColor = texture2D(u_video_clip, uv);
      finalColor = mix(finalColor, clipColor, u_video_clip_blend);
    }

    if (finalColor.a < 0.01 && u_transition < 0.01) {
       vec2 fallbackUV = vUvCorrected;
       fallbackUV -= center;
       fallbackUV /= u_frame_scale;
       fallbackUV += u_frame_offset;
       fallbackUV += center;
       fallbackUV -= center;
       fallbackUV = rotationMatrix * fallbackUV;
       fallbackUV /= (u_scale + 0.2);
       fallbackUV += center;
       finalColor.rgb = fallbackGradient(fallbackUV);
       finalColor.a = 1.0;
    }
    
    if (u_is_capture < 0.5) {
       finalColor.rgb += scanline(vUv, u_time, u_glitch.x);
    }
    finalColor.rgb *= u_flicker;

    vec2 textUV = screenUV - (u_glitch / canvasRes);
    vec4 lyricsSample = texture2D(u_text_texture, textUV);
    if(lyricsSample.a > 0.01) {
      finalColor.rgb = mix(finalColor.rgb, lyricsSample.rgb, lyricsSample.a);
    }
    gl_FragColor = finalColor;
  }
`

const foregroundFragmentShader = `
  varying vec2 vUv;
  uniform sampler2D u_capture_texture;
  uniform sampler2D u_noise_texture;
  uniform vec2 u_canvas_resolution;
  uniform float u_scroll_offset;

  uniform vec4 u_panel_regions[7];
  uniform float u_panel_opacities[7];
  uniform vec4 u_panels_bounding_box;
  uniform float u_header_height;

  uniform vec2 u_radio_button_pos;
  uniform vec2 u_radio_button_radius;
  uniform float u_radio_button_state;
  uniform float u_radio_button_hover;
  uniform float u_radio_button_pressed;
  uniform float u_radio_progress;
  uniform int u_radio_state_int;
  uniform vec3 u_visual_state_color;
  uniform float u_radio_time;
  uniform float u_glass_blur_factor;
  uniform float u_enable_refraction;
  uniform float u_audio_pulse;

  uniform vec3 u_player_gradient_color;
  uniform float u_player_gradient_intensity;

  struct PanelData {
    float mask;
    float depth;
    float edgeGlow;
    float header;
  };

  float sdRoundedBox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - r;
  }

  // Glass effect configuration - hardcoded values matching GLASS_EFFECT_CONFIG
  float getCornerRadius() {
    return 0.015;  // ~1.5% - matches GLASS_EFFECT_CONFIG.cornerRadiusPct
  }

  float getFeatherSize() {
    return 0.01;   // ~1% feather
  }

  PanelData getPanelData(vec2 screenUV, vec4 region, float opacity) {
    PanelData result;
    result.mask = 0.0;
    result.depth = 0.0;
    result.edgeGlow = 0.0;
    result.header = 0.0;
    if (region.z < 0.01 || opacity < 0.01) return result;
    vec2 center = region.xy;
    vec2 size = region.zw * 0.5;
    vec2 p = screenUV - center;
    float cornerRadius = getCornerRadius();
    float feather = getFeatherSize();
    float dist = sdRoundedBox(p, size, cornerRadius);
    result.mask = smoothstep(feather * 0.5, 0.0, dist) * opacity;
    float maxDist = min(size.x, size.y) * 0.7;
    result.depth = smoothstep(0.0, -maxDist, dist) * opacity;
    result.edgeGlow = smoothstep(feather, 0.0, abs(dist)) * opacity;
    if(abs(p.x) < size.x && abs(p.y) < size.y) {
      float transitionZone = 0.01;
      float distFromTopEdge = size.y - p.y;
      result.header = (1.0 - smoothstep(u_header_height - transitionZone, u_header_height + transitionZone, distFromTopEdge)) * opacity;
    }
    return result;
  }

  vec2 calculatePanelRefraction(vec2 screenUV, vec4 region, float depth, float opacity) {
    if (region.z < 0.01 || depth < 0.001 || opacity < 0.01) return vec2(0.0);
    vec2 center = region.xy;
    vec2 size = region.zw * 0.5;
    vec2 p = screenUV - center;
    vec2 radialDir = normalize(p + vec2(0.0001));
    vec2 normalizedP = p / size;
    float distFromCenter = max(abs(normalizedP.x), abs(normalizedP.y));
    float centralBulge = pow(1.0 - distFromCenter, 2.0);
    vec2 bulgeOffset = radialDir * centralBulge * 0.02;
    float edgeKick = pow(distFromCenter, 6.0);
    vec2 edgeOffset = -radialDir * edgeKick * 0.1;
    float noiseScale = 0.75;  
    float noiseStrength = 0.05;
    vec2 worldSpaceUV = (p + center) * noiseScale + vec2(0.0, -u_scroll_offset * 0.0003);
    float noiseX = (texture2D(u_noise_texture, worldSpaceUV).r * 2.0 - 1.0);
    float noiseY = (texture2D(u_noise_texture, worldSpaceUV).g * 2.0 - 1.0);
    vec2 noiseGradient = vec2(noiseX, noiseY) * noiseStrength * depth;
    return (bulgeOffset + edgeOffset + noiseGradient) * opacity;
  }

  vec3 applyPanelEffects(vec3 color, vec2 screenUV, float totalMask, float totalEdgeGlow, float headerFactor, int panelIndex, vec4 region) {
    if(totalMask < 0.001) return color;
    vec3 darkenedColor = color * mix(0.3, 0.2, headerFactor);
    vec3 edgeColor = color * 1.2;
    vec3 resultColor = mix(darkenedColor, edgeColor, totalEdgeGlow);
    vec3 glowColor = vec3(0.6, 0.7, 0.9);
    resultColor += glowColor * totalEdgeGlow * 0.2;

    if (panelIndex == 5 && u_player_gradient_intensity > 0.001) {
      vec2 center = region.xy;
      vec2 size = region.zw * 0.5;
      vec2 p = screenUV - center;

      float normalizedY = (p.y + size.y) / (size.y * 2.0);
      normalizedY = clamp(normalizedY, 0.0, 1.0);

      float gradientShape = pow(1.0 - normalizedY, 1.5);

      vec3 gradientColor = u_player_gradient_color * 0.8;
      float pulseMult = 0.7 + u_audio_pulse * 0.5;
      float gradientOpacity = gradientShape * u_player_gradient_intensity * 0.6 * pulseMult;

      resultColor += gradientColor * gradientOpacity;
    }

    return mix(color, resultColor, totalMask);
  }

  #define PI 3.14159265359

  vec4 calculateRingEmission(vec2 screenUV, vec3 stateColor) {
    if(u_radio_button_radius.x < 0.001 || u_radio_button_radius.y < 0.001) return vec4(0.0);
    
    float opacityMult = u_radio_button_state;
    if(opacityMult < 0.005) return vec4(0.0);
    
    vec2 toCenter = screenUV - u_radio_button_pos;
    vec2 normalizedDist = toCenter / u_radio_button_radius;
    
    float radiusScale = 1.0; 
    
    radiusScale += (u_radio_button_hover * 0.08); 
    if (u_radio_state_int == 1) {
       radiusScale -= 0.08; 
       radiusScale += u_audio_pulse * 0.1;
    }

    vec2 scaledDist = normalizedDist / radiusScale;
    
    float ringRadius = 0.85;
    float dist = length(scaledDist);
    float ringDist = dist - ringRadius;
    
    float ringWidth = 0.05; 
    if (u_radio_state_int == 4) {
        float breath = sin(u_radio_time * 3.0) * 0.5 + 0.5;
        ringWidth = 0.05 + (breath * 0.02);
    }
    
    float ringMask = 1.0 - smoothstep(ringWidth * 0.5, ringWidth * 1.2, abs(ringDist));
    
    bool isFullRing = (u_radio_state_int == 1 || u_radio_state_int == 3 || u_radio_state_int == 4);
    if (!isFullRing && u_radio_progress >= 0.0) {
      vec2 direction = normalize(toCenter);
      float angle = atan(-direction.y, direction.x);
      float normalizedAngle = mod(((angle + PI) / (2.0 * PI)) + 0.25, 1.0);
      float progressMask = step(normalizedAngle, u_radio_progress);
      ringMask *= progressMask;
    }

    vec3 ringColor = stateColor;
    
    float pulseWave = u_audio_pulse; 
    float dynamicGlow = mix(0.5, 2.0, pulseWave);
    float glowFalloff = exp(-abs(ringDist) * 4.0);
    
    if (u_radio_state_int == 2 || u_radio_state_int == 3 || u_radio_state_int == 1) {
        ringColor *= dynamicGlow;
        ringMask *= mix(0.1, 1.0, pulseWave);
    }
    
    if (u_radio_state_int == 4) {
        ringColor *= 1.5;
        ringMask *= 0.8;
    }

    return vec4(ringColor * glowFalloff * 8.0, ringMask * opacityMult);
  }

  vec3 sampleBokehTexture(sampler2D tex, vec2 uv, float blur) {
    if (blur <= 0.001) return texture2D(tex, uv).rgb;
    float cappedBlur = min(blur, 20.0);
    vec3 color = vec3(0.0);
    float radius = cappedBlur * 0.001;
    for (float ang = 0.0; ang < 6.2831853; ang += 2.0943951) {
        vec2 offset = vec2(cos(ang), sin(ang)) * radius;
        color += texture2D(tex, clamp(uv + offset, 0.0, 1.0)).rgb;
    }
    return color / 3.0;
  }

  void main() {
    vec2 screenUV = vUv;
    vec4 finalColor = vec4(0.0); 

    bool inMasterBounds = screenUV.x >= u_panels_bounding_box.x &&
                          screenUV.x <= u_panels_bounding_box.z &&
                          screenUV.y >= u_panels_bounding_box.y &&
                          screenUV.y <= u_panels_bounding_box.w;

    if (inMasterBounds) {
      vec2 refractedScreenUV = vUv;
      float totalPanelMask = 0.0;
      float totalDepth = 0.0;
      float totalEdgeGlow = 0.0;
      vec2 refractionOffset = vec2(0.0);
      float headerFactor = 0.0;
      
      int activePanelIndex = -1;
      vec4 activePanelRegion = vec4(0.0);

      for(int i = 0; i < 7; i++) {
        vec4 region = u_panel_regions[i];
        float opacity = u_panel_opacities[i];
        PanelData pd = getPanelData(screenUV, region, opacity);
        if (pd.mask > 0.001) {
          totalPanelMask = max(totalPanelMask, pd.mask);
          if (pd.mask > 0.5) {
            activePanelIndex = i;
            activePanelRegion = region;
          }
        }
        if (pd.edgeGlow > 0.001) totalEdgeGlow = max(totalEdgeGlow, pd.edgeGlow);
        headerFactor = max(headerFactor, pd.header);
        if (pd.depth > 0.001 && pd.depth > totalDepth) {
          totalDepth = pd.depth;
          if (u_enable_refraction > 0.5) refractionOffset = calculatePanelRefraction(screenUV, region, pd.depth, opacity);
        }
      }

      refractedScreenUV = vUv + refractionOffset * u_enable_refraction;

      if(totalPanelMask > 0.001) {
        vec3 panelColor;
        if (u_glass_blur_factor > 0.001 && u_enable_refraction > 0.5) {
          float blurRadius = 5.0 * totalDepth * u_glass_blur_factor;
          panelColor = sampleBokehTexture(u_capture_texture, refractedScreenUV, blurRadius);
        } else {
          panelColor = texture2D(u_capture_texture, refractedScreenUV).rgb;
        }
        vec3 processedPanel = applyPanelEffects(panelColor, screenUV, totalPanelMask, totalEdgeGlow, headerFactor, activePanelIndex, activePanelRegion);
        finalColor = vec4(processedPanel, totalPanelMask);
      }
    }

    vec2 toCenter = screenUV - u_radio_button_pos;
    vec2 normalizedDist = toCenter / u_radio_button_radius;
    float dist = length(normalizedDist);

    float radiusScale = 1.0; 
    radiusScale += (u_radio_button_hover * 0.08); 
    if (u_radio_state_int == 1) {
       radiusScale -= 0.08; 
       radiusScale += u_audio_pulse * 0.1;
    }
    
    float adjustedDist = dist / radiusScale;
    
    if (adjustedDist < 1.0 && u_radio_button_radius.x > 0.001 && u_radio_button_state > 0.005) {
      float opacityMult = u_radio_button_state;
      float edgeStrength = adjustedDist;
      vec2 radialDir = normalize(toCenter);
      
      float idleState = 0.8;  
      float hoverState = -0.15; 
      float pressState = -1.2; 

      float currentStrength = mix(idleState, hoverState, u_radio_button_hover);
      float baseStrength = mix(currentStrength, pressState, u_radio_button_pressed);
      
      float distortionCurve = smoothstep(0.4, 0.9, edgeStrength) * (1.0 - smoothstep(0.9, 1.0, edgeStrength));
      float refractionStrength = baseStrength * distortionCurve * 0.6 * u_enable_refraction;
      
      vec2 buttonRefraction = radialDir * refractionStrength;
      
      float fresnel = pow(edgeStrength, 2.0);
      vec2 refractedUV = screenUV + buttonRefraction;

      float blurMultiplier = 1.0 - (u_radio_button_hover * 0.5) + (u_radio_button_pressed * 1.2);
      float sphereBlurRadius = 10.0 * edgeStrength * blurMultiplier * u_glass_blur_factor * u_enable_refraction;

      vec3 blurred = sampleBokehTexture(u_capture_texture, refractedUV, sphereBlurRadius);

      float baseGlassBrightness = 0.35 + (u_radio_button_hover * 0.05) - (u_radio_button_pressed * 0.1);
      
      vec3 strictStateColor = u_visual_state_color;
      vec3 glassTint = strictStateColor; 
      if (u_radio_state_int == 0) glassTint = vec3(0.85, 0.9, 1.0);

      vec3 glassColor = blurred * (baseGlassBrightness + fresnel * 0.4); 
      
      float edgeGlow = smoothstep(0.75, 1.0, adjustedDist);
      float glowIntensity = 0.15 + (u_radio_button_hover * 0.2) + (u_radio_button_pressed * 0.6);
      vec3 finalGlowColor = strictStateColor;
      
      if (u_radio_state_int == 0 && u_radio_button_hover < 0.1) {
          glowIntensity = 0.0;
      }

      glassColor += finalGlowColor * edgeGlow * glowIntensity; 
      
      vec4 ringEmission = calculateRingEmission(screenUV, strictStateColor);
      glassColor += ringEmission.rgb * ringEmission.a;

      finalColor = mix(finalColor, vec4(glassColor, 1.0), opacityMult);
    }

    gl_FragColor = finalColor;
  }
`
const transparentPixel = new DataTexture(new Uint8Array([0, 0, 0, 0]), 1, 1, RGBAFormat)
const defaultGeometry = new PlaneGeometry(2, 2)

// Load image and create texture at specified resolution (downscales if needed)
function loadTextureAtResolution(url, targetSize, onLoad) {
  const img = new Image()
  img.crossOrigin = 'anonymous'
  img.onload = () => {
    let texture
    if (targetSize && (img.width > targetSize || img.height > targetSize)) {
      // Downscale to target size
      const canvas = document.createElement('canvas')
      canvas.width = targetSize
      canvas.height = targetSize
      const ctx = canvas.getContext('2d')
      ctx.drawImage(img, 0, 0, targetSize, targetSize)
      texture = new CanvasTexture(canvas)
    } else {
      // Use full resolution
      texture = new CanvasTexture(img)
    }
    texture.wrapS = ClampToEdgeWrapping
    texture.wrapT = ClampToEdgeWrapping
    texture.generateMipmaps = true
    texture.needsUpdate = true
    onLoad(texture)
  }
  img.src = url
}

function generateNoiseTexture() {
  const width = 256
  const height = 256
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  const imageData = ctx.createImageData(width, height)
  const data = imageData.data
  for (let i = 0; i < data.length; i += 4) {
    const value = Math.floor(Math.random() * 255)
    data[i] = value
    data[i + 1] = value
    data[i + 2] = value
    data[i + 3] = 255
  }
  ctx.putImageData(imageData, 0, 0)
  const texture = new CanvasTexture(canvas)
  texture.wrapS = RepeatWrapping
  texture.wrapT = RepeatWrapping
  texture.needsUpdate = true
  return texture
}

function MultiPassPlane({
  currentArtwork,
  transitionProgressRef,
  textTexture,
  noiseTexture,
  captureResolution,
  glassBlurFactor,
  audioFeatures,
  videoClips = [],
  visualQuality = 'high',
}) {
  const videoClipRef = useRef({
    videos: [],
    textures: [],
    currentIndex: 0,
    blend: 0,
    lastCameraCutBeat: -1,
  })

  const {
    shaderPanelRegions: panelRegions,
    shaderPanelOpacities: panelOpacities,
    shaderRadioButtonPos: radioButtonPos,
    radioButtonOpacity,
    radioButtonInteraction,
    radioProgressData,
    speakerColorRef,
    gyroscopeRef,
    mouseRef,
    interfaceRef,
    engineRef,
    engineState,
    isOfflineRendering,
    radioState,
    interfaceState,
    settingsState,
  } = useUIState()

  const isFullscreen = interfaceState?.isFullscreenVisuals ?? false

  const { interactionEffectsRef, getCategoryMetadata } = useDynamicTheme()

  // Register RAF source for debugging
  useEffect(() => {
    window.registerRAFSource?.('ARC-MultiPass')
  }, [])

  const isUnmountedRef = useRef(false)

  const effectsRef = useRef({
    chromatic: 0,
    glitchX: 0,
    glitchY: 0,
    rotation: 0,
    brightness: 0.6,
    saturation: 1,
    contrast: 1,
    scale: 1.0,
    hue: 0,
    blur: 0,
    flicker: 1,
    currentEnergy: 0,
    macroEnergy: 0.5,
    frameOffset: new Vector2(0, 0),
    targetFrameOffset: new Vector2(0, 0),
    frameScale: 1.0,
    targetFrameScale: 1.0,
    beatPulse: 0.0,
  })

  const lastAudioUpdateRef = useRef(0)
  const energyHistoryRef = useRef([])
  const visualCueMapRef = useRef(new Map())
  const lastBeatIndexRef = useRef(0)
  const lastSegmentIndexRef = useRef(0)
  const tempoTimeRef = useRef(0)
  const interpolatedProgressRef = useRef(0)
  const lastSeenProgressRef = useRef(0)
  const localProgressUpdateTimeRef = useRef(Date.now())
  const scratchColorRef = useRef(new Vector3())
  const playerGradientColorRef = useRef(new Vector3(0.0, 0.0, 0.0))
  const targetPlayerGradientColorRef = useRef({ r: 0, g: 0, b: 0 })

  const captureScene = useMemo(() => new Scene(), [])
  const captureCamera = useMemo(() => new OrthographicCamera(-1, 1, 1, -1, 0, 1), [])
  const captureRenderTarget = useMemo(() => {
    return new WebGLRenderTarget(captureResolution, captureResolution, {
      minFilter: LinearFilter,
      magFilter: LinearFilter,
      format: RGBAFormat,
      generateMipmaps: false,
      stencilBuffer: false,
      depthBuffer: false
    })
  }, [captureResolution])

  useEffect(() => {
    return () => captureRenderTarget.dispose()
  }, [captureRenderTarget])

  const bgMaterial = useMemo(() => new ShaderMaterial({
      vertexShader: backgroundVertexShader,
      fragmentShader: backgroundFragmentShader,
      uniforms: {
        u_texture: { value: transparentPixel },
        u_texture_prev: { value: transparentPixel },
        u_depth_map: { value: transparentPixel },
        u_text_texture: { value: transparentPixel },
        u_video_clip: { value: transparentPixel },
        u_video_clip_blend: { value: 0.0 },
        u_transition: { value: 1.0 },
        u_tex_resolution: { value: new Vector2(1, 1) },
        u_canvas_resolution: { value: new Vector2(1, 1) },
        u_parallax: { value: new Vector2(0, 0) },
        u_glitch: { value: new Vector2(0, 0) },
        u_time: { value: 0.0 },
        u_scale: { value: 1.0 },
        u_frame_offset: { value: new Vector2(0, 0) },
        u_frame_scale: { value: 1.0 },
        u_rotation: { value: 0.0 },
        u_brightness: { value: 0.6 },
        u_contrast: { value: 1.0 },
        u_saturation: { value: 1.0 },
        u_hue: { value: 0.0 },
        u_max_blur: { value: 0.0 },
        u_chromatic: { value: 0.0 },
        u_flicker: { value: 1.0 },
        u_has_depth_map: { value: 0.0 },
        u_focal_depth: { value: 0.5 },
        u_focal_range: { value: 0.1 },
        u_is_capture: { value: 0.0 }
      }
  }), [])

  const fgMaterial = useMemo(() => new ShaderMaterial({
      vertexShader: backgroundVertexShader,
      fragmentShader: foregroundFragmentShader,
      uniforms: {
        u_capture_texture: { value: captureRenderTarget.texture },
        u_noise_texture: { value: null },
        u_canvas_resolution: { value: new Vector2(1, 1) },
        u_time: { value: 0.0 },
        u_scroll_offset: { value: 0.0 },
        u_glass_blur_factor: { value: 1.0 },
        u_enable_refraction: { value: 1.0 },
        u_panel_regions: { value: [new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4()] },
        u_panel_opacities: { value: [0,0,0,0,0,0,0] },
        u_panels_bounding_box: { value: new Vector4(0, 0, 0, 0) },
        u_header_height: { value: 0.063 },
        u_radio_button_pos: { value: new Vector2(0.5, 0.5) },
        u_radio_button_radius: { value: new Vector2(0.08, 0.08) },
        u_radio_button_state: { value: 0.0 },
        u_radio_button_hover: { value: 0.0 },
        u_radio_button_pressed: { value: 0.0 },
        u_radio_progress: { value: 0.0 },
        u_radio_state_int: { value: 0 },
        u_visual_state_color: { value: new Vector3(0.5, 0.5, 0.5) },
        u_radio_time: { value: 0.0 },
        u_audio_pulse: { value: 0.0 },
        u_player_gradient_color: { value: new Vector3(0.0, 0.0, 0.0) },
        u_player_gradient_intensity: { value: 0.0 }
      },
      transparent: true
  }), [captureRenderTarget])

  const captureMesh = useMemo(() => new Mesh(defaultGeometry, bgMaterial), [bgMaterial])
  useEffect(() => { captureScene.add(captureMesh); return () => captureScene.remove(captureMesh) }, [captureScene, captureMesh])

  useEffect(() => {
    return () => {
      bgMaterial.dispose()
      fgMaterial.dispose()
      captureMesh.geometry.dispose()
    }
  }, [bgMaterial, fgMaterial, captureMesh])

  const [texA, setTexA] = useState(transparentPixel)
  const [texB, setTexB] = useState(transparentPixel)
  const [frontTex, setFrontTex] = useState('A')
  const previousArtworkRef = useRef(null)

  const smoothProgressRef = useRef(0)

  const panelRegionsRef = useRef([new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4()])
  const panelOpacitiesRef = useRef([0,0,0,0,0,0,0])
  const fgMeshRef = useRef(null)

  const lastLoadedResolutionRef = useRef(null)

  useEffect(() => {
    if (!currentArtwork) return

    // 512px when panels visible, full resolution in fullscreen
    const targetSize = isFullscreen ? null : 512
    const isNewArtwork = currentArtwork !== previousArtworkRef.current
    const isResolutionChange = !isNewArtwork && targetSize !== lastLoadedResolutionRef.current

    if (isNewArtwork) {
      // New artwork: load to back layer, crossfade
      const backLayer = frontTex === 'A' ? 'B' : 'A'
      loadTextureAtResolution(currentArtwork, targetSize, (texture) => {
        if (backLayer === 'A') setTexA(texture); else setTexB(texture);
        requestAnimationFrame(() => { requestAnimationFrame(() => { transitionProgressRef.current = 0; setFrontTex(backLayer) }) })
      })
      previousArtworkRef.current = currentArtwork
      lastLoadedResolutionRef.current = targetSize
    } else if (isResolutionChange) {
      // Same artwork, different resolution: update current layer in place
      loadTextureAtResolution(currentArtwork, targetSize, (texture) => {
        if (frontTex === 'A') setTexA(texture); else setTexB(texture);
      })
      lastLoadedResolutionRef.current = targetSize
    }
  }, [currentArtwork, frontTex, isFullscreen])



  useEffect(() => {
    const mode = radioState.activeSeedMode
    if (mode) {
      const metadata = getCategoryMetadata(mode)
      if (metadata?.color) {
        const hex = metadata.color.replace('#', '')
        const r = parseInt(hex.substring(0, 2), 16)
        const g = parseInt(hex.substring(2, 4), 16)
        const b = parseInt(hex.substring(4, 6), 16)
        targetPlayerGradientColorRef.current = { r, g, b }
      } else {
        targetPlayerGradientColorRef.current = { r: 0, g: 0, b: 0 }
      }
    } else {
      targetPlayerGradientColorRef.current = { r: 0, g: 0, b: 0 }
    }
  }, [radioState.activeSeedMode, getCategoryMetadata])

  useEffect(() => {
    const clipState = videoClipRef.current

    clipState.videos.forEach(v => { v.pause(); v.src = '' })
    clipState.textures.forEach(t => t.dispose())
    clipState.videos = []
    clipState.textures = []
    clipState.currentIndex = 0
    clipState.blend = 0
    clipState.lastCameraCutBeat = -1

    if (!videoClips || videoClips.length === 0) return

    videoClips.forEach(clip => {
      const video = document.createElement('video')
      video.crossOrigin = 'anonymous'
      video.muted = true
      video.loop = true
      video.playsInline = true
      video.src = clip.url
      video.load()
      video.play().catch(() => {})

      const texture = new VideoTexture(video)
      texture.minFilter = LinearFilter
      texture.magFilter = LinearFilter

      clipState.videos.push(video)
      clipState.textures.push(texture)
    })

    return () => {
      clipState.videos.forEach(v => { v.pause(); v.src = '' })
      clipState.textures.forEach(t => t.dispose())
    }
  }, [videoClips])

  useEffect(() => {
    if (audioFeatures) {
        lastBeatIndexRef.current = 0
        lastSegmentIndexRef.current = 0
        const { loudness_segments, beats } = audioFeatures
        if (!loudness_segments || loudness_segments.length === 0 || !beats || beats.length === 0) return
        const newCueMap = new Map()
        let beatCounter = 0
        beats.forEach((beatTime, index) => {
            const cues = new Set()
            if (index % 16 === 0) cues.add('CAMERA_CUT')
            beatCounter++
            if (beatCounter % 2 === 0) cues.add('SMALL_ROTATION')
            if (cues.size > 0) newCueMap.set(beatTime, cues)
        })
        visualCueMapRef.current = newCueMap
        tempoTimeRef.current = 0
    }
  }, [audioFeatures])

  const tex = frontTex === 'A' ? texA : texB
  const prevTex = frontTex === 'A' ? texB : texA

  useEffect(() => {
      bgMaterial.uniforms.u_texture.value = tex
      const img = tex.image
      bgMaterial.uniforms.u_tex_resolution.value.set(img && img.width ? img.width : 1, img && img.height ? img.height : 1)
      bgMaterial.uniforms.u_texture_prev.value = prevTex
      bgMaterial.uniforms.u_has_depth_map.value = 0.0
  }, [tex, prevTex])

  useEffect(() => {
    return () => {
      isUnmountedRef.current = true
    }
  }, [])

  const frameTimingRef = useRef({ total: 0, count: 0, lastLog: 0 })

  useFrame(({ size, gl }, frameDelta) => {
    window.__rafDebug?.sources && (window.__rafDebug.sources['ARC-MultiPass'] = (window.__rafDebug.sources['ARC-MultiPass'] || 0) + 1)
    const frameStart = performance.now()

    if (isOfflineRendering) return

    if (isUnmountedRef.current || !engineRef || !interfaceRef) return

    const now = Date.now()
    const effects = effectsRef.current

    const delta = frameDelta
    const isPlaying = engineState.is_playing
    const progressMs = engineRef.current.progress_ms
    const scrollPosition = interfaceRef.current?.scrollPosition || 0
    const scrollVelocity = interfaceRef.current?.scrollVelocity || 0

    if (now - lastAudioUpdateRef.current > 16) {
        lastAudioUpdateRef.current = now

        if (progressMs !== lastSeenProgressRef.current) {
            localProgressUpdateTimeRef.current = now
            lastSeenProgressRef.current = progressMs
            if (!isPlaying) {
                interpolatedProgressRef.current = progressMs
            }
        }

        if (isPlaying) {
            const timeSinceUpdate = now - localProgressUpdateTimeRef.current
            interpolatedProgressRef.current = progressMs + timeSinceUpdate
        } else {
            interpolatedProgressRef.current = progressMs
        }

        const currentAudioFeatures = audioFeatures
        const tempo = currentAudioFeatures?.tempo || 120
        const beatDurationMs = (60 / tempo) * 1000
        const tempoSyncedDecay = Math.min(1.0, (delta * 1000) / (beatDurationMs * 1.5))
        const fastDecay = Math.min(1.0, (delta * 1000) / (beatDurationMs * 0.8))

        if (isPlaying && currentAudioFeatures?.loudness_segments) {
             const currentTime = interpolatedProgressRef.current / 1000
             const segments = currentAudioFeatures.loudness_segments
             const beats = currentAudioFeatures.beats || []

             let segIdx = lastSegmentIndexRef.current
             while (segIdx < segments.length - 1 && segments[segIdx + 1].start <= currentTime) segIdx++
             lastSegmentIndexRef.current = segIdx
             const currentSegment = segments[segIdx]

             if (currentSegment) {
                const minL = currentAudioFeatures.min_loudness || -60
                const peakL = currentAudioFeatures.peak_loudness || -1
                const rawEnergy = Math.max(0, Math.min(1, (currentSegment.loudness - minL) / (peakL - minL)))

                effects.currentEnergy = rawEnergy

                const energyHistory = energyHistoryRef.current
                energyHistory.push(rawEnergy)
                if (energyHistory.length > 100) energyHistory.shift()
                const sortedEnergy = [...energyHistory].sort((a, b) => a - b)
                const p80 = Math.floor(sortedEnergy.length * 0.80)
                const energyThreshold = Math.max(0.65, sortedEnergy[p80] || 0.65)
                const intensity = Math.max(0, (rawEnergy - energyThreshold) / (1.0 - energyThreshold))

                let onBeat = false
                for (let i = lastBeatIndexRef.current; i < beats.length; i++) {
                    const beatTime = beats[i]
                    if (beatTime > currentTime + 0.08) break
                    if (Math.abs(beatTime - currentTime) < 0.08) {
                        onBeat = true
                        lastBeatIndexRef.current = Math.max(0, i - 1)

                        const cues = visualCueMapRef.current.get(beatTime)
                        if (cues) {
                            if (cues.has('CAMERA_CUT')) {
                                const magnitude = 0.3 + (rawEnergy * 0.4);
                                if (Math.random() < 0.2) {
                                    effects.targetFrameOffset.set(0, 0); effects.targetFrameScale = 1.0;
                                } else {
                                    effects.targetFrameScale = 1.0 + (Math.random() * magnitude);
                                    effects.targetFrameOffset.set((Math.random()-0.5)*magnitude*0.5, (Math.random()-0.5)*magnitude*0.5);
                                }
                                effects.frameOffset.copy(effects.targetFrameOffset);
                                effects.frameScale = effects.targetFrameScale;

                                const clipState = videoClipRef.current
                                if (clipState.textures.length > 0) {
                                    clipState.currentIndex = (clipState.currentIndex + 1) % clipState.textures.length
                                }
                            }
                            if (cues.has('SMALL_ROTATION')) effects.rotation += (Math.random() - 0.5) * 10.0 * rawEnergy
                        }
                        break
                    }
                }

                if (onBeat && rawEnergy > energyThreshold && intensity > 0.4) {
                    effects.glitchX = (Math.random() - 0.5) * intensity * 150.0
                    effects.glitchY = (Math.random() - 0.5) * intensity * 150.0
                    if (intensity > 0.5 && visualQuality === 'high') {
                        effects.hue += intensity * 30.0
                        effects.chromatic = intensity * 80.0
                        effects.blur += intensity * 25.0
                    }
                } else {
                    effects.glitchX *= (1.0 - fastDecay)
                    effects.glitchY *= (1.0 - fastDecay)
                }

                effects.hue *= (1.0 - tempoSyncedDecay)
                effects.chromatic *= (1.0 - fastDecay)
                effects.rotation *= (1.0 - fastDecay)
                effects.brightness += ((0.25 + (rawEnergy * 0.5)) - effects.brightness) * 0.1
                effects.saturation += ((0.8 + (rawEnergy * 0.4)) - effects.saturation) * 0.1
                effects.contrast += ((0.9 + (rawEnergy * 0.2)) - effects.contrast) * 0.1

                const halfSpeedBps = (tempo / 120.0);
                tempoTimeRef.current += delta;
                const breathing = (Math.sin(tempoTimeRef.current * halfSpeedBps * Math.PI * 2.0) + 1.0) / 2.0;

                effects.beatPulse = breathing * (0.2 + rawEnergy * 0.8);
                effects.flicker += ((1.0 - (breathing * rawEnergy * 0.2)) - effects.flicker) * 0.2
                effects.scale += ((1.0 + (breathing * rawEnergy * 0.1)) - effects.scale) * 0.05
             }
        } else {
            effects.chromatic *= (1.0 - fastDecay)
            effects.glitchX *= (1.0 - fastDecay)
            effects.glitchY *= (1.0 - fastDecay)
            effects.rotation += (0 - effects.rotation) * 0.1
            effects.brightness += (0.6 - effects.brightness) * 0.05
            effects.saturation += (1 - effects.saturation) * 0.05
            effects.contrast += (1 - effects.contrast) * 0.05
            effects.scale += (1.0 - effects.scale) * 0.05
            effects.flicker += (1.0 - effects.flicker) * 0.1
            effects.hue += (0 - effects.hue) * 0.1
            effects.targetFrameOffset.set(0, 0);
            effects.targetFrameScale = 1.0;
            effects.frameOffset.copy(effects.targetFrameOffset);
            effects.frameScale = effects.targetFrameScale;
            effects.beatPulse = 0.0;
        }
    }

    const dpr = gl.getPixelRatio()
    const w = size.width * dpr
    const h = size.height * dpr

    fgMaterial.uniforms.u_header_height.value = PANEL.headerHeight / size.height

    let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
    let hasVisiblePanels = false;
    const panelCount = panelRegions ? Math.min(panelRegions.length, 7) : 0;

    for (let i = 0; i < panelCount; i++) {
        const region = panelRegions[i];
        const current = panelRegionsRef.current[i];
        const targetOpacity = panelOpacities[i] || 0.0;

        current.x = region.x;
        current.y = region.y;
        current.z = region.z;
        current.w = region.w;

        panelOpacitiesRef.current[i] += (targetOpacity - panelOpacitiesRef.current[i]) * 0.3;

        if (current.z > 0.01 && panelOpacitiesRef.current[i] > 0.01) {
          hasVisiblePanels = true;
          const halfWidth = current.z * 0.5;
          const halfHeight = current.w * 0.5;
          minX = Math.min(minX, current.x - halfWidth);
          minY = Math.min(minY, current.y - halfHeight);
          maxX = Math.max(maxX, current.x + halfWidth);
          maxY = Math.max(maxY, current.y + halfHeight);
        }
        fgMaterial.uniforms.u_panel_regions.value[i].copy(current)
        fgMaterial.uniforms.u_panel_opacities.value[i] = panelOpacitiesRef.current[i]
    }
    if (hasVisiblePanels) {
      fgMaterial.uniforms.u_panels_bounding_box.value.set(
        Math.max(0.0, minX - 0.02), Math.max(0.0, minY - 0.02),
        Math.min(1.0, maxX + 0.02), Math.min(1.0, maxY + 0.02)
      );
    } else {
      fgMaterial.uniforms.u_panels_bounding_box.value.set(0,0,0,0);
    }

    if (radioButtonPos && radioButtonPos.radiusX > 0 && radioButtonPos.radiusY > 0) {
       const currentPos = fgMaterial.uniforms.u_radio_button_pos.value;
       const currentRadius = fgMaterial.uniforms.u_radio_button_radius.value;

       currentPos.x = radioButtonPos.x;
       currentPos.y = radioButtonPos.y;
       currentRadius.x = radioButtonPos.radiusX;
       currentRadius.y = radioButtonPos.radiusY;
    }

    if (radioButtonOpacity !== undefined) {
        const currentOpacity = fgMaterial.uniforms.u_radio_button_state.value;
        // Sync with React CSS transition (0.5s) - approx 0.15 per frame at 60fps
        fgMaterial.uniforms.u_radio_button_state.value += (radioButtonOpacity - currentOpacity) * 0.15;
    }

    if (fgMaterial.uniforms.u_radio_button_state.value > 0.01) hasVisiblePanels = true;

    if (radioButtonInteraction) {
        const targetHover = radioButtonInteraction.isHovered ? 1.0 : 0.0;
        const targetPressed = radioButtonInteraction.isPressed ? 1.0 : 0.0;

        const currentHover = fgMaterial.uniforms.u_radio_button_hover.value;
        const currentPressed = fgMaterial.uniforms.u_radio_button_pressed.value;

        fgMaterial.uniforms.u_radio_button_hover.value += (targetHover - currentHover) * 0.25;
        fgMaterial.uniforms.u_radio_button_pressed.value += (targetPressed - currentPressed) * 0.25;
    }

    if (radioProgressData) {
        const currentTrack = engineState.currentTrack
        let progressPercent = 0
        if (currentTrack && currentTrack.duration_ms > 0) {
            progressPercent = Math.min(100, Math.max(0, (progressMs / currentTrack.duration_ms) * 100))
        }

        const targetProgress = progressPercent / 100;
        smoothProgressRef.current = MathUtils.lerp(smoothProgressRef.current, targetProgress, 0.1);

        const stateInt = radioProgressData.stateInt || 0;
        fgMaterial.uniforms.u_radio_progress.value = smoothProgressRef.current;
        fgMaterial.uniforms.u_radio_state_int.value = stateInt;
        fgMaterial.uniforms.u_radio_time.value += delta;

        let c = radioProgressData.currentVisualColor;

        if (stateInt === 3 && speakerColorRef && speakerColorRef.current) {
            c = speakerColorRef.current;
        }

        if (c) {
            scratchColorRef.current.set(c.r/255, c.g/255, c.b/255);
            fgMaterial.uniforms.u_visual_state_color.value.lerp(scratchColorRef.current, 5.0 * delta);
        }
    }

    fgMaterial.uniforms.u_glass_blur_factor.value = visualQuality === 'high' ? glassBlurFactor : 0
    fgMaterial.uniforms.u_enable_refraction.value = visualQuality === 'high' ? 1.0 : 0.0
    fgMaterial.uniforms.u_audio_pulse.value = effects.beatPulse
    fgMaterial.uniforms.u_scroll_offset.value = scrollPosition

    const targetGradientColor = targetPlayerGradientColorRef.current
    scratchColorRef.current.set(targetGradientColor.r / 255, targetGradientColor.g / 255, targetGradientColor.b / 255)
    fgMaterial.uniforms.u_player_gradient_color.value.lerp(scratchColorRef.current, 2.0 * delta)

    const hasGradient = targetGradientColor.r > 0 || targetGradientColor.g > 0 || targetGradientColor.b > 0
    const targetIntensity = hasGradient ? 1.0 : 0.0
    const currentIntensity = fgMaterial.uniforms.u_player_gradient_intensity.value
    fgMaterial.uniforms.u_player_gradient_intensity.value += (targetIntensity - currentIntensity) * 0.05

    if (interactionEffectsRef && visualQuality === 'high') {
      const click = interactionEffectsRef.current.click
      if (click.active) {
        const age = performance.now() - click.timestamp
        const decay = Math.max(0, 1 - age / 500)

        if (decay > 0) {
          const intensity = click.intensity * decay
          effects.glitchX += (Math.random() - 0.5) * intensity * 100.0
          effects.glitchY += (Math.random() - 0.5) * intensity * 100.0
          effects.chromatic += intensity * 40.0
          effects.rotation += (Math.random() - 0.5) * intensity * 8.0
          const targetBrightness = 0.6 + (intensity * 0.3)
          effects.brightness += (targetBrightness - effects.brightness) * 0.3
        } else {
          click.active = false
        }
      }
    }

    effects.blur *= 0.9;

    if (scrollVelocity > 0.01 && visualQuality === 'high') {
      effects.blur += scrollVelocity * 50.0
    }

    const gyro = gyroscopeRef?.current || { parallaxX: 0, parallaxY: 0 }
    const mouse = mouseRef?.current || { parallaxX: 0, parallaxY: 0 }
    const hasGyro = Math.abs(gyro.parallaxX) > 0.001 || Math.abs(gyro.parallaxY) > 0.001
    const pX = hasGyro ? gyro.parallaxX * 40 : mouse.parallaxX * 40
    const pY = hasGyro ? gyro.parallaxY * 40 : mouse.parallaxY * 40

    if (transitionProgressRef.current < 1) {
        transitionProgressRef.current = Math.min(1, transitionProgressRef.current + delta / 0.7)
    }

    bgMaterial.uniforms.u_time.value = (bgMaterial.uniforms.u_time.value + delta) % 1000.0
    bgMaterial.uniforms.u_transition.value = transitionProgressRef.current
    bgMaterial.uniforms.u_parallax.value.set(pX, pY)
    bgMaterial.uniforms.u_glitch.value.set(effects.glitchX, effects.glitchY)
    bgMaterial.uniforms.u_frame_offset.value.set(effects.frameOffset.x, effects.frameOffset.y)
    bgMaterial.uniforms.u_frame_scale.value = effects.frameScale
    bgMaterial.uniforms.u_scale.value = effects.scale || 1.0
    bgMaterial.uniforms.u_rotation.value = effects.rotation * Math.PI / 180
    bgMaterial.uniforms.u_brightness.value = effects.brightness
    bgMaterial.uniforms.u_contrast.value = effects.contrast
    bgMaterial.uniforms.u_saturation.value = effects.saturation
    bgMaterial.uniforms.u_hue.value = effects.hue * Math.PI / 180
    bgMaterial.uniforms.u_max_blur.value = visualQuality === 'high' ? effects.blur : 0
    bgMaterial.uniforms.u_chromatic.value = visualQuality === 'high' ? effects.chromatic : 0
    bgMaterial.uniforms.u_has_depth_map.value = 0.0
    bgMaterial.uniforms.u_flicker.value = effects.flicker

    const clipState = videoClipRef.current
    if (clipState.textures.length > 0 && visualQuality === 'high') {
      const currentEnergy = effectsRef.current.currentEnergy || 0

      const targetBlend = isPlaying
        ? 0.15 + (currentEnergy * 0.55)
        : 0

      const blendSpeed = targetBlend > clipState.blend ? 0.15 : 0.08
      clipState.blend += (targetBlend - clipState.blend) * blendSpeed

      const playbackRate = 0.6 + (currentEnergy * 1.2)
      const currentVideo = clipState.videos[clipState.currentIndex]
      if (currentVideo && currentVideo.readyState >= 2) {
        currentVideo.playbackRate = playbackRate
      }

      const currentTexture = clipState.textures[clipState.currentIndex]
      if (currentTexture) {
        currentTexture.needsUpdate = true
        bgMaterial.uniforms.u_video_clip.value = currentTexture
        bgMaterial.uniforms.u_video_clip_blend.value = clipState.blend
      }
    } else {
      bgMaterial.uniforms.u_video_clip_blend.value = 0
    }

    if (noiseTexture) fgMaterial.uniforms.u_noise_texture.value = noiseTexture

    const rtAspect = w / h
    const rtHeight = captureResolution
    const rtWidth = Math.floor(rtHeight * rtAspect)
    if (Math.abs(captureRenderTarget.width - rtWidth) > 1 || Math.abs(captureRenderTarget.height - rtHeight) > 1) {
        captureRenderTarget.setSize(rtWidth, rtHeight)
    }

    bgMaterial.uniforms.u_canvas_resolution.value.set(rtWidth, rtHeight)
    if (textTexture) bgMaterial.uniforms.u_text_texture.value = textTexture

    if (hasVisiblePanels) {
        bgMaterial.uniforms.u_is_capture.value = 1.0
        gl.setRenderTarget(captureRenderTarget)
        gl.render(captureScene, captureCamera)
        gl.setRenderTarget(null)
        bgMaterial.uniforms.u_is_capture.value = 0.0
    }

    bgMaterial.uniforms.u_canvas_resolution.value.set(w, h)
    fgMaterial.uniforms.u_canvas_resolution.value.set(w, h)

    if (fgMeshRef.current) {
      fgMeshRef.current.visible = hasVisiblePanels
    }

    // DEBUG: Frame timing measurement
    const frameEnd = performance.now()
    const frameTime = frameEnd - frameStart
    frameTimingRef.current.total += frameTime
    frameTimingRef.current.count++

    // DEBUG: Performance diagnostics - log every second
    if (now - frameTimingRef.current.lastLog > 1000) {
      const avgFrameTime = frameTimingRef.current.total / frameTimingRef.current.count
      const debugInfo = {
        avgFrameTimeMs: avgFrameTime.toFixed(2),
        framesInSecond: frameTimingRef.current.count,
        hasVisiblePanels,
        radioBtn: fgMaterial.uniforms.u_radio_button_state.value.toFixed(3),
        panelOpacities: panelOpacitiesRef.current.map(o => o.toFixed(2)).join(','),
        captureRan: hasVisiblePanels ? 'YES' : 'no',
        videoClipsActive: clipState.textures.length > 0 && visualQuality === 'high',
        videoBlend: clipState.blend.toFixed(2),
        gyroActive: Math.abs(gyro.parallaxX) > 0.001 || Math.abs(gyro.parallaxY) > 0.001,
        gyroValues: `${gyro.parallaxX?.toFixed(3)},${gyro.parallaxY?.toFixed(3)}`,
        mouseValues: `${mouse.parallaxX?.toFixed(3)},${mouse.parallaxY?.toFixed(3)}`,
        visualQuality,
        dpr: gl.getPixelRatio().toFixed(2),
      }
      if (settingsState.fpsEnabled) {
        console.log('[SHADER PERF]', debugInfo)
      }
      frameTimingRef.current.total = 0
      frameTimingRef.current.count = 0
      frameTimingRef.current.lastLog = now
    }
  })

  return (
    <>
      <mesh geometry={defaultGeometry} material={bgMaterial} renderOrder={0} />
      <mesh ref={fgMeshRef} geometry={defaultGeometry} material={fgMaterial} renderOrder={1} />
    </>
  )
}

const LyricsRenderer = memo(function LyricsRenderer({
  lyricDataRef,
  lyricCanvasRef,
  lyricTextureRef,
  lastWordRef,
}) {
  const { engineRef, engineState, isOfflineRendering, isScreenVisible } = useUIState()
  const intervalRef = useRef(null)
  const lastWordIndexRef = useRef(-1)

  // Poll at 10fps only when playing and screen visible
  useEffect(() => {
    if (isOfflineRendering) return
    if (!engineRef) return
    if (!isScreenVisible) return

    const updateLyric = () => {
      const lyricData = lyricDataRef.current
      if (!lyricData || lyricData.length === 0) return
      if (!lyricCanvasRef.current || !lyricTextureRef.current) return

      const progressMs = engineRef.current.progress_ms || 0
      const currentTimeSec = progressMs / 1000

      // Find current word index
      const currentIndex = lyricData.findIndex(w =>
        currentTimeSec >= w.start && currentTimeSec <= w.end
      )

      // Only redraw if word changed
      if (currentIndex !== lastWordIndexRef.current) {
        lastWordIndexRef.current = currentIndex

        const canvas = lyricCanvasRef.current
        const ctx = canvas.getContext('2d')
        const texture = lyricTextureRef.current

        const textToDraw = currentIndex >= 0 ? lyricData[currentIndex].text : null

        renderLyricToCanvas(ctx, textToDraw, canvas.width, canvas.height)
        texture.needsUpdate = true
        lastWordRef.current = textToDraw
      }
    }

    updateLyric()

    if (engineState.is_playing) {
      intervalRef.current = setInterval(updateLyric, 100)
    }

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [engineState.is_playing, lyricDataRef, lyricCanvasRef, lyricTextureRef, lastWordRef, isOfflineRendering, engineRef, isScreenVisible])

  return null
})

export const AudioReactiveCanvas = memo(function AudioReactiveCanvas({
  depthMap,
  captureResolution = 128,
  glassBlurFactor = 1.0,
}) {
  const { currentArtwork } = useDynamicTheme()
  const { audioFeatures, lyricTimestamps, engineState, isOfflineRendering, settingsState } = useUIState()
  const visualQuality = settingsState.visualQuality || 'high'

  const currentTrackId = engineState?.currentTrack?.id
  const videoClips = useVideoClips(currentTrackId)

  const [textTexture, setTextTexture] = useState(null)
  const transitionProgressRef = useRef(1)

  const textRendererRef = useRef(null)
  const processedLyricDataRef = useRef(null)
  const lyricCanvasRef = useRef(null)
  const lyricTextureRef = useRef(null)
  const lastWordRef = useRef(null)

  const noiseTexture = useMemo(() => generateNoiseTexture(), [])

  useEffect(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 1024
    canvas.height = 512
    lyricCanvasRef.current = canvas
    const ctx = canvas.getContext('2d', { alpha: true })
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    const texture = new CanvasTexture(canvas)
    texture.needsUpdate = true
    lyricTextureRef.current = texture
    setTextTexture(texture)
    return () => {
      if (lyricTextureRef.current) {
        lyricTextureRef.current.dispose()
      }
      setTextTexture(null)
    }
  }, [])

  useEffect(() => {
    lastWordRef.current = null
    const canvas = lyricCanvasRef.current
    if (canvas) {
      const ctx = canvas.getContext('2d')
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      if (lyricTextureRef.current) lyricTextureRef.current.needsUpdate = true
    }
    if (!lyricTimestamps || lyricTimestamps.instrumental) {
      processedLyricDataRef.current = null; return
    }
    if (!textRendererRef.current) textRendererRef.current = new TextRenderer()
    const renderer = textRendererRef.current
    const { words, wordData } = renderer.prepareLyricData(lyricTimestamps)
    if (words.length === 0) {
      processedLyricDataRef.current = null; return
    }
    processedLyricDataRef.current = wordData
  }, [lyricTimestamps])

  if (isOfflineRendering) {
    return null
  }

  return (
    <>
      <div className="absolute inset-0 bg-black/50 pointer-events-none z-0" />
      <Canvas
        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 0 }}
        gl={{ antialias: false, powerPreference: 'high-performance', stencil: false, depth: false, alpha: true }}
        dpr={Math.min(window.devicePixelRatio || 1, visualQuality === 'high' ? 1.5 : 1.0)}
      >
        <MultiPassPlane
          currentArtwork={currentArtwork}
          transitionProgressRef={transitionProgressRef}
          textTexture={textTexture}
          noiseTexture={noiseTexture}
          captureResolution={captureResolution}
          glassBlurFactor={glassBlurFactor}
          audioFeatures={audioFeatures}
          videoClips={videoClips}
          visualQuality={visualQuality}
        />
        <LyricsRenderer
          lyricDataRef={processedLyricDataRef}
          textRendererRef={textRendererRef}
          lyricCanvasRef={lyricCanvasRef}
          lyricTextureRef={lyricTextureRef}
          lastWordRef={lastWordRef}
        />
      </Canvas>
    </>
  )
})

export function createInitialEffects() {
  return {
    chromatic: 0,
    glitchX: 0,
    glitchY: 0,
    rotation: 0,
    brightness: 0.6,
    saturation: 1,
    contrast: 1,
    scale: 1.0,
    hue: 0,
    blur: 0,
    flicker: 1,
    currentEnergy: 0,
    macroEnergy: 0.5,
    frameOffsetX: 0,
    frameOffsetY: 0,
    targetFrameOffsetX: 0,
    targetFrameOffsetY: 0,
    frameScale: 1.0,
    targetFrameScale: 1.0,
    beatPulse: 0.0,
  }
}

export function buildVisualCueMap(beats) {
  const cueMap = new Map()
  if (!beats || beats.length === 0) return cueMap

  beats.forEach((beatTime, index) => {
    const cues = new Set()
    if (index % 16 === 0) cues.add('CAMERA_CUT')
    if (index % 2 === 0) cues.add('SMALL_ROTATION')
    if (cues.size > 0) cueMap.set(beatTime, cues)
  })
  return cueMap
}

export function calculateFrameEffects({
  effects,
  audioFeatures,
  currentTimeSeconds,
  delta,
  visualCueMap,
  trackingState,
  random,
  onCameraCut,
}) {
  if (!audioFeatures?.loudness_segments) {
    const fastDecay = 0.1
    effects.chromatic *= (1.0 - fastDecay)
    effects.glitchX *= (1.0 - fastDecay)
    effects.glitchY *= (1.0 - fastDecay)
    effects.rotation += (0 - effects.rotation) * 0.1
    effects.brightness += (0.6 - effects.brightness) * 0.05
    effects.saturation += (1 - effects.saturation) * 0.05
    effects.contrast += (1 - effects.contrast) * 0.05
    effects.scale += (1.0 - effects.scale) * 0.05
    effects.flicker += (1.0 - effects.flicker) * 0.1
    effects.hue += (0 - effects.hue) * 0.1
    effects.targetFrameOffsetX = 0
    effects.targetFrameOffsetY = 0
    effects.targetFrameScale = 1.0
    effects.frameOffsetX = 0
    effects.frameOffsetY = 0
    effects.frameScale = 1.0
    effects.beatPulse = 0.0
    effects.currentEnergy = 0
    return 0
  }

  const tempo = audioFeatures.tempo || 120
  const beatDurationMs = (60 / tempo) * 1000
  const tempoSyncedDecay = Math.min(1.0, (delta * 1000) / (beatDurationMs * 1.5))
  const fastDecay = Math.min(1.0, (delta * 1000) / (beatDurationMs * 0.8))

  const segments = audioFeatures.loudness_segments
  const beats = audioFeatures.beats || []
  const currentTime = currentTimeSeconds

  while (trackingState.lastSegmentIndex < segments.length - 1 &&
         segments[trackingState.lastSegmentIndex + 1].start <= currentTime) {
    trackingState.lastSegmentIndex++
  }
  const currentSegment = segments[trackingState.lastSegmentIndex]

  if (!currentSegment) return 0

  const minL = audioFeatures.min_loudness || -60
  const peakL = audioFeatures.peak_loudness || -1
  const rawEnergy = Math.max(0, Math.min(1, (currentSegment.loudness - minL) / (peakL - minL)))
  effects.currentEnergy = rawEnergy

  trackingState.energyHistory.push(rawEnergy)
  if (trackingState.energyHistory.length > 100) trackingState.energyHistory.shift()
  const sortedEnergy = [...trackingState.energyHistory].sort((a, b) => a - b)
  const p80 = Math.floor(sortedEnergy.length * 0.80)
  const energyThreshold = Math.max(0.65, sortedEnergy[p80] || 0.65)
  const intensity = Math.max(0, (rawEnergy - energyThreshold) / (1.0 - energyThreshold))

  let onBeat = false
  for (let i = trackingState.lastBeatIndex; i < beats.length; i++) {
    const beatTime = beats[i]
    if (beatTime > currentTime + 0.08) break
    if (Math.abs(beatTime - currentTime) < 0.08) {
      onBeat = true
      trackingState.lastBeatIndex = Math.max(0, i - 1)

      const cues = visualCueMap.get(beatTime)
      if (cues) {
        if (cues.has('CAMERA_CUT')) {
          if (onCameraCut) onCameraCut()

          const magnitude = 0.3 + (rawEnergy * 0.4)
          if (random() < 0.2) {
            effects.targetFrameOffsetX = 0
            effects.targetFrameOffsetY = 0
            effects.targetFrameScale = 1.0
          } else {
            effects.targetFrameScale = 1.0 + (random() * magnitude)
            effects.targetFrameOffsetX = (random() - 0.5) * magnitude * 0.5
            effects.targetFrameOffsetY = (random() - 0.5) * magnitude * 0.5
          }
          effects.frameOffsetX = effects.targetFrameOffsetX
          effects.frameOffsetY = effects.targetFrameOffsetY
          effects.frameScale = effects.targetFrameScale
        }
        if (cues.has('SMALL_ROTATION')) {
          effects.rotation += (random() - 0.5) * 10.0 * rawEnergy
        }
      }
      break
    }
  }

  if (onBeat && rawEnergy > energyThreshold && intensity > 0.4) {
    effects.glitchX = (random() - 0.5) * intensity * 150.0
    effects.glitchY = (random() - 0.5) * intensity * 150.0
    if (intensity > 0.5) {
      effects.hue += intensity * 30.0
      effects.chromatic = intensity * 80.0
      effects.blur += intensity * 25.0
    }
  } else {
    effects.glitchX *= (1.0 - fastDecay)
    effects.glitchY *= (1.0 - fastDecay)
  }

  effects.hue *= (1.0 - tempoSyncedDecay)
  effects.chromatic *= (1.0 - fastDecay)
  effects.rotation *= (1.0 - fastDecay)
  effects.brightness += ((0.25 + (rawEnergy * 0.5)) - effects.brightness) * 0.1
  effects.saturation += ((0.8 + (rawEnergy * 0.4)) - effects.saturation) * 0.1
  effects.contrast += ((0.9 + (rawEnergy * 0.2)) - effects.contrast) * 0.1

  const halfSpeedBps = tempo / 120.0
  trackingState.tempoTime += delta
  const breathing = (Math.sin(trackingState.tempoTime * halfSpeedBps * Math.PI * 2.0) + 1.0) / 2.0

  effects.beatPulse = breathing * (0.2 + rawEnergy * 0.8)
  effects.flicker += ((1.0 - (breathing * rawEnergy * 0.2)) - effects.flicker) * 0.2
  effects.scale += ((1.0 + (breathing * rawEnergy * 0.1)) - effects.scale) * 0.05

  return rawEnergy
}

export function processLyricTimestamps(data) {
  if (!data?.lyrics) return []
  const words = []
  for (const line of data.lyrics) {
    for (const word of line.words || []) {
      words.push({
        text: word.word,
        start: word.start,
        end: word.end,
      })
    }
  }
  return words
}

export function getLyricAt(lyrics, timeMs, startIndex = 0) {
  if (!lyrics?.length) return null
  const t = timeMs / 1000
  for (let i = startIndex; i < lyrics.length; i++) {
    const w = lyrics[i]
    if (t >= w.start && t <= w.end) return w.text
    if (w.start > t) break
  }
  return null
}

export function renderLyricToCanvas(ctx, text, width, height) {
  ctx.clearRect(0, 0, width, height)
  if (text) {
    ctx.fillStyle = 'white'
    ctx.font = '900 180px Inter, sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(text, width / 2, height / 2)
  }
}

export { backgroundVertexShader, backgroundFragmentShader }