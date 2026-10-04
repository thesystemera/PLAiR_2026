import {memo, useEffect, useMemo, useRef, useState} from 'react'
import {Canvas, useFrame, useThree} from '@react-three/fiber'
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
import { useVideoClips, useUISelector } from '../contexts/UIStateContext'
import {PANEL, useDynamicTheme, useThemeArtwork} from '../contexts/DynamicThemeContext'
import {VisualErrorBoundary} from './VisualErrorBoundary'
import {isWebGL2Available} from '../lib/utils'
import {logger} from '../lib/logger'
import {REFERENCE_SCENE_DPR, useQuality} from '../contexts/QualityContext'
import {isSceneRenderingPaused} from '../lib/renderPause'
import {splashReady} from '../lib/splash'
import {PROBE_GRID, lightProbeWanted, publishBeat, publishLightLevel, setLightGlow} from '../lib/lightProbe'
import {LightProbeReader} from '../lib/lightProbeReader'

const backgroundVertexShader = `
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
    gl_Position = vec4(position, 1.0);
  }
`
const BACKGROUND_FRAGMENT_BODY = `
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

  const vec2 BOKEH_DIR_1 = vec2(1.0, 0.0);
  const vec2 BOKEH_DIR_2 = vec2(-0.5, 0.8660254);
  const vec2 BOKEH_DIR_3 = vec2(-0.5, -0.8660254);

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

  vec4 sampleBlend(vec2 uv, float transition) {
    return mix(texture2D(u_texture_prev, uv), texture2D(u_texture, uv), transition);
  }

  vec4 sampleBokeh(vec2 uv, float blur, float transition) {
    float radius = min(blur, 60.0) * 0.0016;
    vec4 color = sampleBlend(clamp(uv + BOKEH_DIR_1 * radius, 0.0, 1.0), transition);
    color += sampleBlend(clamp(uv + BOKEH_DIR_2 * radius, 0.0, 1.0), transition);
    color += sampleBlend(clamp(uv + BOKEH_DIR_3 * radius, 0.0, 1.0), transition);
    return color / 3.0;
  }

  vec4 sampleBokehCurrent(vec2 uv, float blur) {
    float radius = min(blur, 60.0) * 0.0016;
    vec4 color = texture2D(u_texture, clamp(uv + BOKEH_DIR_1 * radius, 0.0, 1.0));
    color += texture2D(u_texture, clamp(uv + BOKEH_DIR_2 * radius, 0.0, 1.0));
    color += texture2D(u_texture, clamp(uv + BOKEH_DIR_3 * radius, 0.0, 1.0));
    return color / 3.0;
  }

  vec4 applyEffects(vec2 uv, float blurAmount, float transition) {
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
    color.rgb = (color.rgb - 0.5) * u_contrast + 0.5;
    color.rgb *= u_brightness;
    float luma = dot(color.rgb, vec3(0.299, 0.587, 0.114));
    color.rgb = mix(vec3(luma), color.rgb, u_saturation);
    if (abs(u_hue) > 0.001) {
      float cosHue = vHue.x;
      float sinHue = vHue.y;
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

`
const BACKGROUND_FRAGMENT_MAIN = `  void main() {
    vec2 uv = vFxUv;
    float blurAmount = getBlurAmount(uv);
    vec4 finalColor;

    if (abs(u_chromatic) > 0.5 && u_is_capture < 0.5) {
      vec2 chromaticOffset = vec2(u_chromatic, 0.0) / u_canvas_resolution;
      vec4 colorR = applyEffects(uv + chromaticOffset, blurAmount, u_transition);
      vec4 colorG = applyEffects(uv, blurAmount, u_transition);
      vec4 colorB = applyEffects(uv - chromaticOffset, blurAmount, u_transition);
      finalColor = vec4(colorR.r, colorG.g, colorB.b, colorG.a);
    } else {
      finalColor = applyEffects(uv, blurAmount, u_transition);
    }

    if (u_video_clip_blend > 0.01) {
      vec4 clipColor = texture2D(u_video_clip, uv);
      finalColor = mix(finalColor, clipColor, u_video_clip_blend);
    }

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
    gl_FragColor = finalColor;
  }
`
const backgroundFragmentShader = BACKGROUND_FRAGMENT_BODY + BACKGROUND_FRAGMENT_MAIN

const GLASS_FRAGMENT_BODY = `
  uniform sampler2D u_capture_texture;
  uniform sampler2D u_noise_texture;
  uniform float u_glass_taps;
  uniform float u_scroll_offset;

  uniform vec4 u_panel_regions[7];
  uniform float u_panel_opacities[7];
  uniform float u_panel_ids[7];
  uniform int u_panel_count;
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
  uniform vec3 u_panel_glow;

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

  float getCornerRadius() {
    return 0.015;
  }

  float getFeatherSize() {
    return 0.01;
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
    if (abs(p.x) > size.x + feather || abs(p.y) > size.y + feather) return result;
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
    float bulgeBase = 1.0 - distFromCenter;
    float centralBulge = bulgeBase * bulgeBase;
    vec2 bulgeOffset = radialDir * centralBulge * 0.02;
    float distSq = distFromCenter * distFromCenter;
    float edgeKick = distSq * distSq * distSq;
    vec2 edgeOffset = -radialDir * edgeKick * 0.1;
    float noiseScale = 0.75;  
    float noiseStrength = 0.05;
    vec2 worldSpaceUV = (p + center) * noiseScale + vec2(0.0, -u_scroll_offset * 0.0003);
    vec2 noiseSample = texture2D(u_noise_texture, worldSpaceUV).rg * 2.0 - 1.0;
    vec2 noiseGradient = noiseSample * noiseStrength * depth;
    return (bulgeOffset + edgeOffset + noiseGradient) * opacity;
  }

  vec3 applyPanelEffects(vec3 color, vec2 screenUV, float totalMask, float totalEdgeGlow, float headerFactor, int panelIndex, vec4 region) {
    if(totalMask < 0.001) return color;
    vec3 darkenedColor = color * mix(0.3, 0.2, headerFactor);
    vec3 edgeColor = color * 1.2;
    vec3 resultColor = mix(darkenedColor, edgeColor, totalEdgeGlow);
    resultColor += u_panel_glow * totalEdgeGlow;

    if (panelIndex == 5 && u_player_gradient_intensity > 0.001) {
      vec2 center = region.xy;
      vec2 size = region.zw * 0.5;
      vec2 p = screenUV - center;

      float normalizedY = (p.y + size.y) / (size.y * 2.0);
      normalizedY = clamp(normalizedY, 0.0, 1.0);

      float gradientBase = 1.0 - normalizedY;
      float gradientShape = gradientBase * sqrt(gradientBase);

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
    float radius = min(blur, 20.0) * 0.001;
    vec3 color = texture2D(tex, clamp(uv + vec2(1.0, 0.0) * radius, 0.0, 1.0)).rgb;
    color += texture2D(tex, clamp(uv + vec2(-0.5, 0.8660254) * radius, 0.0, 1.0)).rgb;
    color += texture2D(tex, clamp(uv + vec2(-0.5, -0.8660254) * radius, 0.0, 1.0)).rgb;
    return color / 3.0;
  }

  vec4 computeGlass() {
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
      vec4 deepestRegion = vec4(0.0);
      float deepestOpacity = 0.0;

      for(int i = 0; i < 7; i++) {
        if (i >= u_panel_count) break;
        vec4 region = u_panel_regions[i];
        float opacity = u_panel_opacities[i];
        PanelData pd = getPanelData(screenUV, region, opacity);
        if (pd.mask > 0.001) {
          totalPanelMask = max(totalPanelMask, pd.mask);
          if (pd.mask > 0.5) {
            activePanelIndex = int(u_panel_ids[i] + 0.5);
            activePanelRegion = region;
          }
        }
        if (pd.edgeGlow > 0.001) totalEdgeGlow = max(totalEdgeGlow, pd.edgeGlow);
        headerFactor = max(headerFactor, pd.header);
        if (pd.depth > 0.001 && pd.depth > totalDepth) {
          totalDepth = pd.depth;
          deepestRegion = region;
          deepestOpacity = opacity;
        }
      }

      if (totalDepth > 0.001 && u_enable_refraction > 0.5) {
        refractionOffset = calculatePanelRefraction(screenUV, deepestRegion, totalDepth, deepestOpacity);
      }

      refractedScreenUV = vUv + refractionOffset * u_enable_refraction;

      if(totalPanelMask > 0.001) {
        vec3 panelColor;
        if (u_glass_taps > 1.5 && u_glass_blur_factor > 0.001 && u_enable_refraction > 0.5) {
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
      
      float fresnel = edgeStrength * edgeStrength;
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

    return finalColor;
  }
`

const AMBIENT_GLOW_FUNCTION = `
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

const BACKGROUND_FUNCTION = `
  uniform vec3 u_underlay;

` + BACKGROUND_FRAGMENT_MAIN.replace('void main() {', 'vec4 computeBackground() {').replace('gl_FragColor = finalColor;', 'return finalColor;')

const backdropFragmentShader = BACKGROUND_FRAGMENT_BODY + BACKGROUND_FUNCTION + AMBIENT_GLOW_FUNCTION + `
  void main() {
    vec4 background = clamp(computeBackground(), 0.0, 1.0);
    gl_FragColor = vec4(applyAmbientGlow(background.rgb + (1.0 - background.a) * u_underlay, vUv, 0.0), 1.0);
  }
`

const sceneFragmentShader = BACKGROUND_FRAGMENT_BODY + GLASS_FRAGMENT_BODY + BACKGROUND_FUNCTION + AMBIENT_GLOW_FUNCTION + `
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
const probeVertexShader = `
  void main() {
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`

const probeFragmentShader = `
  uniform sampler2D u_source;

  void main() {
    vec2 cell = floor(gl_FragCoord.xy);
    vec3 sum = vec3(0.0);
    for (int y = 0; y < 3; y++) {
      for (int x = 0; x < 3; x++) {
        vec2 uv = (cell + (vec2(float(x), float(y)) + 0.5) / 3.0) / ${PROBE_GRID}.0;
        sum += texture2D(u_source, uv).rgb;
      }
    }
    gl_FragColor = vec4(sum / 9.0, 1.0);
  }
`

const transparentPixel = new DataTexture(new Uint8Array([0, 0, 0, 0]), 1, 1, RGBAFormat)
const defaultGeometry = new PlaneGeometry(2, 2)

const numericAscending = (a, b) => a - b

function lowerBound(sorted, value) {
  let lo = 0
  let hi = sorted.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (sorted[mid] < value) lo = mid + 1
    else hi = mid
  }
  return lo
}

function pushEnergySample(history, stamps, sorted, value, now) {
  if (sorted.length !== history.length) {
    sorted.length = 0
    for (let i = 0; i < history.length; i++) sorted.push(history[i])
    sorted.sort(numericAscending)
  }
  history.push(value)
  stamps.push(now)
  sorted.splice(lowerBound(sorted, value), 0, value)
  while (stamps.length > 1 && now - stamps[0] > ENERGY_WINDOW_MS) {
    stamps.shift()
    sorted.splice(lowerBound(sorted, history.shift()), 1)
  }
  return Math.max(0.65, sorted[Math.floor(sorted.length * 0.80)] || 0.65)
}

const BG_SIGNATURE_UNIFORMS = [
  'u_video_clip_blend', 'u_transition', 'u_tex_resolution', 'u_glitch', 'u_scale',
  'u_frame_offset', 'u_frame_scale', 'u_rotation', 'u_brightness', 'u_contrast', 'u_saturation',
  'u_hue', 'u_max_blur', 'u_chromatic', 'u_flicker', 'u_canvas_resolution', 'u_text_empty',
]

const FG_SIGNATURE_UNIFORMS = [
  'u_scroll_offset', 'u_panel_regions', 'u_panel_opacities', 'u_panel_ids', 'u_panel_count', 'u_panels_bounding_box', 'u_header_height',
  'u_radio_button_pos', 'u_radio_button_radius', 'u_radio_button_state', 'u_radio_button_hover',
  'u_radio_button_pressed', 'u_radio_progress', 'u_radio_state_int', 'u_visual_state_color',
  'u_glass_blur_factor', 'u_enable_refraction', 'u_audio_pulse', 'u_player_gradient_color',
  'u_player_gradient_intensity', 'u_glass_taps',
  'u_glow_center', 'u_glow_scale', 'u_voice_glow', 'u_on_air_glow', 'u_panel_glow', 'u_glow_active',
]

const GLOW_FALLOFF_SCALE = Math.sqrt(6 / Math.LN2)
const NO_PARALLAX = Object.freeze({ parallaxX: 0, parallaxY: 0 })

const VOICE_ATTACK = 18
const VOICE_RELEASE = 5
const VOICE_GAIN = 2.2
const VOICE_STEADY = 0.4
const VOICE_COLOR_RATE = 4
const ON_AIR_RATE = 2.5
const ON_AIR_PAUSED = 0.5
const ON_AIR_BREATHE_SECONDS = 2.4
const AMBIENT_FLOOR = 0.002
const CROSSFADE_GLOW = 0.7
const CROSSFADE_GLOW_MIN_MS = 1000
const CROSSFADE_RELEASE = 1.2

const approach = (current, target, rate, delta) => current + (target - current) * (1 - Math.exp(-rate * delta))

const PROBE_INTERVAL_MS = 66
const REFERENCE_FPS = 60
const ENERGY_WINDOW_MS = 1667
const LIGHT_CURVE_STEP_S = 0.5
const LIGHT_CURVE_WINDOW_S = 4
const LIGHT_IN_RANK = 0.55
const LIGHT_FULL_RANK = 0.85
const LIGHT_ATTACK_RATE = 1.2
const LIGHT_RELEASE_RATE = 0.35
const LIGHT_WITHOUT_ANALYSIS = 0.5
const LIGHT_KICK_FLOOR = 0.25

function buildLightCurve(features) {
  const segments = features?.loudness_segments
  if (!segments?.length) return null
  const last = segments[segments.length - 1]
  const bins = Math.max(1, Math.ceil((features.duration || last.start + last.duration) / LIGHT_CURVE_STEP_S))
  const sum = new Float32Array(bins)
  const count = new Float32Array(bins)
  for (const segment of segments) {
    const bin = Math.min(bins - 1, Math.floor(segment.start / LIGHT_CURVE_STEP_S))
    sum[bin] += segment.loudness
    count[bin]++
  }
  const loudness = Float32Array.from(sum, (total, i) => (count[i] ? total / count[i] : -60))
  const half = Math.round(LIGHT_CURVE_WINDOW_S / LIGHT_CURVE_STEP_S / 2)
  const smooth = Float32Array.from(loudness, (_, i) => {
    let total = 0
    let n = 0
    for (let j = Math.max(0, i - half); j <= Math.min(bins - 1, i + half); j++) {
      total += loudness[j]
      n++
    }
    return total / n
  })
  const sorted = Array.from(smooth).sort(numericAscending)
  return Float32Array.from(smooth, value => MathUtils.smoothstep(lowerBound(sorted, value) / bins, LIGHT_IN_RANK, LIGHT_FULL_RANK))
}

function averageLevel(data) {
  if (!data || !data.length) return 0
  let sum = 0
  for (let i = 0; i < data.length; i++) sum += data[i]
  return Math.min(1, (sum / data.length / 255) * VOICE_GAIN)
}

const SIGNATURE_EPSILON = 1e-4
const SIGNATURE_SIZE = 160
const PARALLAX_SIGNATURE_SCALE = SIGNATURE_EPSILON / 0.05
const UNDERLAY_LEVEL = 0x0a / 255 * 0.5
const FRAME_CAP_SLACK_SECONDS = 0.004

function writeSignatureValue(out, index, value) {
  if (typeof value === 'number') {
    out[index] = value
    return index + 1
  }
  if (Array.isArray(value)) {
    let next = index
    for (let i = 0; i < value.length; i++) next = writeSignatureValue(out, next, value[i])
    return next
  }
  out[index] = value.x
  out[index + 1] = value.y
  if (value.isVector2) return index + 2
  out[index + 2] = value.z
  if (value.isVector3) return index + 3
  out[index + 3] = value.w
  return index + 4
}

function writeUniformSignature(out, index, uniforms, names) {
  let next = index
  for (let i = 0; i < names.length; i++) next = writeSignatureValue(out, next, uniforms[names[i]].value)
  return next
}

function signatureChanged(current, previous, length) {
  for (let i = 0; i < length; i++) {
    if (Math.abs(current[i] - previous[i]) > SIGNATURE_EPSILON) return true
  }
  return false
}

function loadTextureAtResolution(url, targetSize, onLoad, onError) {
  const img = new Image()
  img.crossOrigin = 'anonymous'
  img.decoding = 'async'
  let settled = false
  img.onerror = (event) => {
    if (settled) return
    settled = true
    onError?.(event)
  }
  const build = () => {
    if (settled) return
    settled = true
    let texture
    if (targetSize && (img.width > targetSize || img.height > targetSize)) {
      const canvas = document.createElement('canvas')
      canvas.width = targetSize
      canvas.height = targetSize
      const ctx = canvas.getContext('2d')
      ctx.drawImage(img, 0, 0, targetSize, targetSize)
      texture = new CanvasTexture(canvas)
    } else {
      texture = new CanvasTexture(img)
    }
    texture.wrapS = ClampToEdgeWrapping
    texture.wrapT = ClampToEdgeWrapping
    texture.generateMipmaps = true
    texture.needsUpdate = true
    onLoad(texture)
  }
  img.onload = () => {
    if (typeof img.decode === 'function') {
      img.decode().then(build, build)
    } else {
      build()
    }
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

function createLyricResources() {
  const canvas = document.createElement('canvas')
  canvas.width = 1024
  canvas.height = 512
  const ctx = canvas.getContext('2d', { alpha: true })
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  const texture = new CanvasTexture(canvas)
  texture.userData.empty = true
  texture.needsUpdate = true
  return { canvas, texture }
}

function releaseVideo(video) {
  video.pause()
  video.removeAttribute('src')
  video.load()
}

function syncVideoClipPlayback(clipState) {
  const count = clipState.videos.length
  if (count === 0) return
  const nextIndex = (clipState.currentIndex + 1) % count
  clipState.videos.forEach((video, index) => {
    if (index === clipState.currentIndex || index === nextIndex) {
      if (!video.getAttribute('src')) {
        video.src = video.dataset.clipUrl
        video.load()
      }
      if (video.paused) video.play().catch(() => {})
    } else if (!video.paused) {
      video.pause()
    }
  })
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
  referenceDpr = 1,
}) {
  const videoClipRef = useRef({
    videos: [],
    textures: [],
    currentIndex: 0,
    blend: 0,
    lastCameraCutBeat: -1,
  })

  const {
    panelRegionsRef,
    panelOpacitiesRef,
    radioButtonPosRef,
    radioButtonRef,
    radioProgressData,
    speakerColorRef,
    djFftDataRef,
    gyroscopeRef,
    mouseRef,
    interfaceRef,
    engineRef,
    engineState,
    isOfflineRendering,
    radioState,
    interfaceState,
    settingsState,
  } = useUISelector(state => ({
    panelRegionsRef: state.shaderPanelRegions,
    panelOpacitiesRef: state.shaderPanelOpacities,
    radioButtonPosRef: state.shaderRadioButtonPos,
    radioButtonRef: state.radioButtonRef,
    radioProgressData: state.radioProgressData,
    speakerColorRef: state.speakerColorRef,
    djFftDataRef: state.djFftDataRef,
    gyroscopeRef: state.gyroscopeRef,
    mouseRef: state.mouseRef,
    interfaceRef: state.interfaceRef,
    engineRef: state.engineRef,
    engineState: state.engineState,
    isOfflineRendering: state.isOfflineRendering,
    radioState: state.radioState,
    interfaceState: state.interfaceState,
    settingsState: state.settingsState,
  }))

  const isFullscreen = interfaceState?.isFullscreenVisuals ?? false

  const { interactionEffectsRef, getCategoryMetadata, getAccentRgb } = useDynamicTheme()
  const { fpsCap, glassTaps, reduceMotion, reportFrame, reportRenderer, tier } = useQuality()
  const renderer = useThree(state => state.gl)
  const mainScene = useThree(state => state.scene)
  const mainCamera = useThree(state => state.camera)
  const programsReadyRef = useRef(false)

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

  const beatStateRef = useRef({ threshold: 0.65, intensity: 0, onBeat: false })
  const energyHistoryRef = useRef([])
  const energyScratchRef = useRef([])
  const energyStampsRef = useRef([])
  const renderSignatureRef = useRef({
    current: new Float64Array(SIGNATURE_SIZE),
    previous: new Float64Array(SIGNATURE_SIZE),
    length: 0,
    textures: [null, null, null, null],
    textVersion: -1,
    fgVisible: false,
    force: true,
    captureStale: true,
    captureLength: 0,
    captured: new Float64Array(SIGNATURE_SIZE),
  })
  const lastDrawAtRef = useRef(0)
  const visualCueMapRef = useRef(new Map())
  const lastBeatIndexRef = useRef(0)
  const lastSegmentIndexRef = useRef(0)
  const tempoTimeRef = useRef(0)
  const interpolatedProgressRef = useRef(0)
  const lastSeenProgressRef = useRef(0)
  const localProgressUpdateTimeRef = useRef(Date.now())
  const scratchColorRef = useRef(new Vector3())
  const targetPlayerGradientColorRef = useRef({ r: 0, g: 0, b: 0 })
  const ambientRef = useRef({ voice: 0, onAir: 0, breatheTime: 0, crossfade: 0 })
  const crossfadeColorRef = useRef(new Vector3())
  const accentRgbRef = useRef(getAccentRgb())
  useEffect(() => { accentRgbRef.current = getAccentRgb() }, [getAccentRgb])

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
        u_text_empty: { value: 0.0 },
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
      fragmentShader: sceneFragmentShader,
      uniforms: {
        ...bgMaterial.uniforms,
        u_capture_texture: { value: captureRenderTarget.texture },
        u_noise_texture: { value: null },
        u_glass_taps: { value: 3.0 },
        u_underlay: { value: new Vector3(UNDERLAY_LEVEL, UNDERLAY_LEVEL, UNDERLAY_LEVEL) },
        u_scroll_offset: { value: 0.0 },
        u_glass_blur_factor: { value: 1.0 },
        u_enable_refraction: { value: 1.0 },
        u_panel_regions: { value: [new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4()] },
        u_panel_opacities: { value: [0,0,0,0,0,0,0] },
        u_panel_ids: { value: [0,0,0,0,0,0,0] },
        u_panel_count: { value: 0 },
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
        u_player_gradient_intensity: { value: 0.0 },
        u_voice_color: { value: new Vector3(0.58, 0.2, 0.92) },
        u_voice_level: { value: 0.0 },
        u_on_air_color: { value: new Vector3(0.96, 0.62, 0.04) },
        u_on_air: { value: 0.0 },
        u_glow_center: { value: new Vector2(0.5, 0.55) },
        u_glow_scale: { value: new Vector2(GLOW_FALLOFF_SCALE, GLOW_FALLOFF_SCALE) },
        u_voice_glow: { value: new Vector3(0, 0, 0) },
        u_on_air_glow: { value: new Vector3(0, 0, 0) },
        u_panel_glow: { value: new Vector3(0.12, 0.14, 0.18) },
        u_glow_active: { value: 0.0 }
      }
  }), [bgMaterial, captureRenderTarget])

  const backdropMaterial = useMemo(() => new ShaderMaterial({
      vertexShader: backgroundVertexShader,
      fragmentShader: backdropFragmentShader,
      uniforms: {
        ...bgMaterial.uniforms,
        u_underlay: fgMaterial.uniforms.u_underlay,
        u_glow_center: fgMaterial.uniforms.u_glow_center,
        u_glow_scale: fgMaterial.uniforms.u_glow_scale,
        u_voice_glow: fgMaterial.uniforms.u_voice_glow,
        u_on_air_glow: fgMaterial.uniforms.u_on_air_glow,
        u_glow_active: fgMaterial.uniforms.u_glow_active,
      }
  }), [bgMaterial, fgMaterial])

  const glassMeshRef = useRef(null)
  const backdropMeshRef = useRef(null)

  const captureMesh = useMemo(() => new Mesh(defaultGeometry, bgMaterial), [bgMaterial])
  useEffect(() => { captureScene.add(captureMesh); return () => captureScene.remove(captureMesh) }, [captureScene, captureMesh])

  const probeScene = useMemo(() => new Scene(), [])
  const probeTarget = useMemo(() => new WebGLRenderTarget(PROBE_GRID, PROBE_GRID, {
    minFilter: LinearFilter,
    magFilter: LinearFilter,
    format: RGBAFormat,
    generateMipmaps: false,
    stencilBuffer: false,
    depthBuffer: false
  }), [])
  const probeMaterial = useMemo(() => new ShaderMaterial({
    vertexShader: probeVertexShader,
    fragmentShader: probeFragmentShader,
    uniforms: { u_source: { value: captureRenderTarget.texture } }
  }), [captureRenderTarget])
  const probeMesh = useMemo(() => new Mesh(defaultGeometry, probeMaterial), [probeMaterial])
  useEffect(() => { probeScene.add(probeMesh); return () => probeScene.remove(probeMesh) }, [probeScene, probeMesh])
  useEffect(() => () => { probeMaterial.dispose() }, [probeMaterial])
  useEffect(() => () => { probeTarget.dispose() }, [probeTarget])
  const probeReaderRef = useRef(null)
  const probeAtRef = useRef(0)
  useEffect(() => {
    const reader = new LightProbeReader(renderer.getContext())
    probeReaderRef.current = reader
    return () => {
      probeReaderRef.current = null
      reader.dispose()
    }
  }, [renderer])
  const lightCurveRef = useRef(null)
  const lightLevelRef = useRef(0)
  const lastKickBeatRef = useRef(-1)

  const glowAt = useMemo(() => {
    const out = [0, 0, 0]
    const u = fgMaterial.uniforms
    return (x, y) => {
      if (u.u_glow_active.value < 0.5) {
        out[0] = 0; out[1] = 0; out[2] = 0
        return out
      }
      const dx = (x - u.u_glow_center.value.x) * u.u_glow_scale.value.x
      const dy = (y - u.u_glow_center.value.y) * u.u_glow_scale.value.y
      const voice = Math.pow(2, -(dx * dx + dy * dy))
      const edge = MathUtils.smoothstep(Math.max(Math.abs(x - 0.5), Math.abs(y - 0.5)), 0.225, 0.5)
      const air = 0.06 + 0.2 * edge
      const voiceGlow = u.u_voice_glow.value
      const onAirGlow = u.u_on_air_glow.value
      out[0] = onAirGlow.x * air + voiceGlow.x * voice
      out[1] = onAirGlow.y * air + voiceGlow.y * voice
      out[2] = onAirGlow.z * air + voiceGlow.z * voice
      return out
    }
  }, [fgMaterial])
  useEffect(() => setLightGlow(glowAt), [glowAt])

  useEffect(() => {
    let cancelled = false
    Promise.all([
      renderer.compileAsync(captureScene, captureCamera),
      renderer.compileAsync(mainScene, mainCamera),
    ]).then(() => {
      if (!cancelled) programsReadyRef.current = true
    })
    return () => { cancelled = true }
  }, [renderer, mainScene, mainCamera, captureScene, captureCamera, captureMesh, fgMaterial, backdropMaterial])

  useEffect(() => {
    try {
      const context = renderer.getContext()
      const info = context.getExtension('WEBGL_debug_renderer_info')
      reportRenderer(String(context.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : context.RENDERER) || ''))
    } catch {
      reportRenderer('')
    }
  }, [renderer, reportRenderer])

  useEffect(() => {
    return () => {
      bgMaterial.dispose()
      fgMaterial.dispose()
      backdropMaterial.dispose()
      captureMesh.geometry.dispose()
    }
  }, [bgMaterial, fgMaterial, backdropMaterial, captureMesh])

  const [texA, setTexA] = useState(transparentPixel)
  const [texB, setTexB] = useState(transparentPixel)
  const [frontTex, setFrontTex] = useState('A')
  const previousArtworkRef = useRef(null)
  const textureLoadTokensRef = useRef({ A: 0, B: 0 })

  useEffect(() => () => {
    if (texA !== transparentPixel) texA.dispose()
  }, [texA])

  useEffect(() => () => {
    if (texB !== transparentPixel) texB.dispose()
  }, [texB])

  const smoothProgressRef = useRef(0)

  const panelRegionsVecsRef = useRef([new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4(), new Vector4()])
  const panelOpacitiesLerpRef = useRef([0,0,0,0,0,0,0])

  const lastLoadedResolutionRef = useRef(null)

  useEffect(() => {
    if (!currentArtwork) return

    const targetSize = isFullscreen ? null : 512
    const isNewArtwork = currentArtwork !== previousArtworkRef.current
    const isResolutionChange = !isNewArtwork && targetSize !== lastLoadedResolutionRef.current

    const loadIntoLayer = (layer, onApplied) => {
      const tokens = textureLoadTokensRef.current
      const token = ++tokens[layer]
      loadTextureAtResolution(currentArtwork, targetSize, (texture) => {
        if (tokens[layer] !== token) {
          texture.dispose()
          return
        }
        if (layer === 'A') setTexA(texture); else setTexB(texture);
        onApplied?.()
      }, () => {
        if (tokens[layer] === token) {
          logger.warn('[AudioReactiveCanvas] Failed to load artwork texture')
        }
      })
    }

    if (isNewArtwork) {
      const backLayer = frontTex === 'A' ? 'B' : 'A'
      loadIntoLayer(backLayer, () => {
        requestAnimationFrame(() => { requestAnimationFrame(() => { transitionProgressRef.current = 0; setFrontTex(backLayer) }) })
      })
      previousArtworkRef.current = currentArtwork
      lastLoadedResolutionRef.current = targetSize
    } else if (isResolutionChange) {
      loadIntoLayer(frontTex)
      lastLoadedResolutionRef.current = targetSize
    }
  }, [currentArtwork, frontTex, isFullscreen, transitionProgressRef])



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

    clipState.videos.forEach(releaseVideo)
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
      video.preload = 'auto'
      video.dataset.clipUrl = clip.url

      const texture = new VideoTexture(video)
      texture.minFilter = LinearFilter
      texture.magFilter = LinearFilter

      clipState.videos.push(video)
      clipState.textures.push(texture)
    })

    syncVideoClipPlayback(clipState)

    return () => {
      clipState.videos.forEach(releaseVideo)
      clipState.textures.forEach(t => t.dispose())
      clipState.videos = []
      clipState.textures = []
    }
  }, [videoClips])

  useEffect(() => {
    lightCurveRef.current = buildLightCurve(audioFeatures)
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
  }, [bgMaterial, tex, prevTex])

  useEffect(() => {
    return () => {
      isUnmountedRef.current = true
    }
  }, [])

  const frameTimingRef = useRef({ total: 0, count: 0, lastLog: 0 })

  useFrame(({ size, gl, scene, camera }, frameDelta) => {
    window.__rafDebug?.sources && (window.__rafDebug.sources['ARC-MultiPass'] = (window.__rafDebug.sources['ARC-MultiPass'] || 0) + 1)
    const frameStart = performance.now()

    if (isOfflineRendering) return

    if (isUnmountedRef.current || !engineRef || !interfaceRef || !programsReadyRef.current) return

    probeReaderRef.current?.collect()

    const now = Date.now()
    const effects = effectsRef.current

    const delta = frameDelta
    const isPlaying = engineState.is_playing
    const progressMs = engineRef.current.progress_ms
    const scrollPosition = interfaceRef.current?.scrollPosition || 0
    const scrollVelocity = interfaceRef.current?.scrollVelocity || 0

    const perFrame = rate => 1 - Math.pow(1 - rate, delta * REFERENCE_FPS)

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

            const beatState = beatStateRef.current
            beatState.threshold = pushEnergySample(energyHistoryRef.current, energyStampsRef.current, energyScratchRef.current, rawEnergy, now)
            beatState.intensity = Math.max(0, (rawEnergy - beatState.threshold) / (1.0 - beatState.threshold))

            let onBeat = false
            let beatHit = -1
            for (let i = lastBeatIndexRef.current; i < beats.length; i++) {
                const beatTime = beats[i]
                if (beatTime > currentTime + 0.08) break
                if (Math.abs(beatTime - currentTime) < 0.08) {
                    onBeat = true
                    beatHit = beatTime
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
                                syncVideoClipPlayback(clipState)
                            }
                        }
                        if (cues.has('SMALL_ROTATION')) effects.rotation += (Math.random() - 0.5) * 10.0 * rawEnergy
                    }
                    break
                }
            }
            beatState.onBeat = onBeat && rawEnergy > beatState.threshold && beatState.intensity > 0.4

            if (beatState.onBeat) {
                effects.glitchX = (Math.random() - 0.5) * beatState.intensity * 150.0
                effects.glitchY = (Math.random() - 0.5) * beatState.intensity * 150.0
                if (beatState.intensity > 0.5 && visualQuality === 'high') {
                    effects.hue += beatState.intensity * 30.0
                    effects.chromatic = beatState.intensity * 80.0
                    effects.blur += beatState.intensity * 25.0
                }
            }

            const kick = onBeat && beatHit !== lastKickBeatRef.current && rawEnergy > beatState.threshold
            if (kick) lastKickBeatRef.current = beatHit

            if (!beatState.onBeat) {
                effects.glitchX *= (1.0 - fastDecay)
                effects.glitchY *= (1.0 - fastDecay)
            }

            effects.hue *= (1.0 - tempoSyncedDecay)
            effects.chromatic *= (1.0 - fastDecay)
            effects.rotation *= (1.0 - fastDecay)
            effects.brightness += ((0.25 + (rawEnergy * 0.5)) - effects.brightness) * perFrame(0.1)
            effects.saturation += ((0.8 + (rawEnergy * 0.4)) - effects.saturation) * perFrame(0.1)
            effects.contrast += ((0.9 + (rawEnergy * 0.2)) - effects.contrast) * perFrame(0.1)

            const halfSpeedBps = (tempo / 120.0);
            tempoTimeRef.current += delta;
            const breathing = (Math.sin(tempoTimeRef.current * halfSpeedBps * Math.PI * 2.0) + 1.0) / 2.0;

            effects.beatPulse = breathing * (0.2 + rawEnergy * 0.8);
            effects.flicker += ((1.0 - (breathing * rawEnergy * 0.2)) - effects.flicker) * perFrame(0.2)
            effects.scale += ((1.0 + (breathing * rawEnergy * 0.1)) - effects.scale) * perFrame(0.05)

            const lightUp = lightLevelRef.current >= LIGHT_KICK_FLOOR
            publishBeat(reduceMotion || !kick || !lightUp ? 0 : Math.min(1, 0.5 + beatState.intensity * 0.5), reduceMotion ? 0 : effects.beatPulse)
         }
    } else {
        effects.chromatic *= (1.0 - fastDecay)
        effects.glitchX *= (1.0 - fastDecay)
        effects.glitchY *= (1.0 - fastDecay)
        effects.rotation += (0 - effects.rotation) * perFrame(0.1)
        effects.brightness += (0.6 - effects.brightness) * perFrame(0.05)
        effects.saturation += (1 - effects.saturation) * perFrame(0.05)
        effects.contrast += (1 - effects.contrast) * perFrame(0.05)
        effects.scale += (1.0 - effects.scale) * perFrame(0.05)
        effects.flicker += (1.0 - effects.flicker) * perFrame(0.1)
        effects.hue += (0 - effects.hue) * perFrame(0.1)
        effects.targetFrameOffset.set(0, 0);
        effects.targetFrameScale = 1.0;
        effects.frameOffset.copy(effects.targetFrameOffset);
        effects.frameScale = effects.targetFrameScale;
        effects.beatPulse = 0.0;
        beatStateRef.current.onBeat = false
        publishBeat(0, 0)
    }

    const curve = lightCurveRef.current
    const lightTarget = !isPlaying ? 0 : curve
      ? curve[Math.min(curve.length - 1, Math.floor(interpolatedProgressRef.current / 1000 / LIGHT_CURVE_STEP_S))]
      : LIGHT_WITHOUT_ANALYSIS
    const lightRate = lightTarget > lightLevelRef.current ? LIGHT_ATTACK_RATE : LIGHT_RELEASE_RATE
    lightLevelRef.current = approach(lightLevelRef.current, lightTarget, lightRate, delta)
    if (lightLevelRef.current < 0.002 && lightTarget === 0) lightLevelRef.current = 0
    publishLightLevel(lightLevelRef.current, lightTarget)

    const dpr = gl.getPixelRatio()
    const w = size.width * dpr
    const h = size.height * dpr
    const logicalWidth = size.width * referenceDpr
    const logicalHeight = size.height * referenceDpr

    fgMaterial.uniforms.u_header_height.value = PANEL.headerHeight / size.height

    let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
    let hasVisiblePanels = false;
    const regions = panelRegionsRef.current
    const opacities = panelOpacitiesRef.current
    const panelCount = regions ? Math.min(regions.length, 7) : 0;
    const packedRegions = fgMaterial.uniforms.u_panel_regions.value
    const packedOpacities = fgMaterial.uniforms.u_panel_opacities.value
    const packedIds = fgMaterial.uniforms.u_panel_ids.value
    let packedCount = 0

    for (let i = 0; i < panelCount; i++) {
        const region = regions[i];
        const current = panelRegionsVecsRef.current[i];
        const targetOpacity = opacities[i] || 0.0;

        current.x = region.x;
        current.y = region.y;
        current.z = region.z;
        current.w = region.w;

        panelOpacitiesLerpRef.current[i] += (targetOpacity - panelOpacitiesLerpRef.current[i]) * 0.3;

        if (current.z > 0.01 && panelOpacitiesLerpRef.current[i] > 0.01) {
          hasVisiblePanels = true;
          const halfWidth = current.z * 0.5;
          const halfHeight = current.w * 0.5;
          minX = Math.min(minX, current.x - halfWidth);
          minY = Math.min(minY, current.y - halfHeight);
          maxX = Math.max(maxX, current.x + halfWidth);
          maxY = Math.max(maxY, current.y + halfHeight);
        }
        if (current.z >= 0.01 && panelOpacitiesLerpRef.current[i] >= 0.01) {
          packedRegions[packedCount].copy(current)
          packedOpacities[packedCount] = panelOpacitiesLerpRef.current[i]
          packedIds[packedCount] = i
          packedCount++
        }
    }
    for (let i = packedCount; i < 7; i++) {
      packedRegions[i].set(0, 0, 0, 0)
      packedOpacities[i] = 0
      packedIds[i] = 0
    }
    fgMaterial.uniforms.u_panel_count.value = packedCount
    if (hasVisiblePanels) {
      fgMaterial.uniforms.u_panels_bounding_box.value.set(
        Math.max(0.0, minX - 0.02), Math.max(0.0, minY - 0.02),
        Math.min(1.0, maxX + 0.02), Math.min(1.0, maxY + 0.02)
      );
    } else {
      fgMaterial.uniforms.u_panels_bounding_box.value.set(0,0,0,0);
    }

    const rbPos = radioButtonPosRef.current
    if (rbPos && rbPos.radiusX > 0 && rbPos.radiusY > 0) {
       const currentPos = fgMaterial.uniforms.u_radio_button_pos.value;
       const currentRadius = fgMaterial.uniforms.u_radio_button_radius.value;

       currentPos.x = rbPos.x;
       currentPos.y = rbPos.y;
       currentRadius.x = rbPos.radiusX;
       currentRadius.y = rbPos.radiusY;
    }

    const radioButton = radioButtonRef.current

    fgMaterial.uniforms.u_radio_button_state.value += (radioButton.opacity - fgMaterial.uniforms.u_radio_button_state.value) * 0.15;

    if (fgMaterial.uniforms.u_radio_button_state.value > 0.01) hasVisiblePanels = true;

    const targetHover = radioButton.isHovered ? 1.0 : 0.0;
    const targetPressed = radioButton.isPressed ? 1.0 : 0.0;
    fgMaterial.uniforms.u_radio_button_hover.value += (targetHover - fgMaterial.uniforms.u_radio_button_hover.value) * 0.25;
    fgMaterial.uniforms.u_radio_button_pressed.value += (targetPressed - fgMaterial.uniforms.u_radio_button_pressed.value) * 0.25;

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

    const ambient = ambientRef.current
    const onAirColor = radioProgressData?.onAirColor || null
    const speaking = radioProgressData?.stateInt === 3
    const steadyAmbient = reduceMotion || tier === 0
    const voiceTarget = speaking ? (steadyAmbient ? VOICE_STEADY : averageLevel(djFftDataRef?.current)) : 0
    ambient.voice = approach(ambient.voice, voiceTarget, voiceTarget > ambient.voice ? VOICE_ATTACK : VOICE_RELEASE, delta)
    if (ambient.voice < AMBIENT_FLOOR && voiceTarget === 0) ambient.voice = 0
    fgMaterial.uniforms.u_voice_level.value = ambient.voice
    if (speaking && speakerColorRef?.current) {
      const sc = speakerColorRef.current
      scratchColorRef.current.set(sc.r / 255, sc.g / 255, sc.b / 255)
      fgMaterial.uniforms.u_voice_color.value.lerp(scratchColorRef.current, Math.min(1, VOICE_COLOR_RATE * delta))
    }

    const talkBreak = engineState.talkBreak
    const onAirTarget = talkBreak ? (talkBreak.paused ? ON_AIR_PAUSED : 1) : 0
    ambient.onAir = approach(ambient.onAir, onAirTarget, ON_AIR_RATE, delta)
    if (ambient.onAir < AMBIENT_FLOOR && onAirTarget === 0) ambient.onAir = 0
    let breathe = 1
    if (ambient.onAir > 0 && !talkBreak?.paused && !reduceMotion && tier >= 2) {
      ambient.breatheTime += delta
      breathe = 0.8 + 0.2 * Math.sin(ambient.breatheTime * Math.PI * 2 / ON_AIR_BREATHE_SECONDS)
    }
    fgMaterial.uniforms.u_on_air.value = ambient.onAir * breathe

    const crossfadeMs = engineState.crossfadeMs || 0
    const crossfadeTarget = crossfadeMs >= CROSSFADE_GLOW_MIN_MS ? CROSSFADE_GLOW : 0
    const crossfadeRate = crossfadeTarget > ambient.crossfade ? 4000 / Math.max(crossfadeMs, 1) : CROSSFADE_RELEASE
    ambient.crossfade = approach(ambient.crossfade, crossfadeTarget, crossfadeRate, delta)
    if (ambient.crossfade < AMBIENT_FLOOR && crossfadeTarget === 0) ambient.crossfade = 0
    const accent = accentRgbRef.current
    scratchColorRef.current.set(accent.r / 255, accent.g / 255, accent.b / 255)
    crossfadeColorRef.current.lerp(scratchColorRef.current, Math.min(1, VOICE_COLOR_RATE * delta))
    if (onAirColor) {
      scratchColorRef.current.set(onAirColor.r / 255, onAirColor.g / 255, onAirColor.b / 255)
      fgMaterial.uniforms.u_on_air_color.value.lerp(scratchColorRef.current, Math.min(1, VOICE_COLOR_RATE * delta))
    }

    fgMaterial.uniforms.u_glass_blur_factor.value = visualQuality === 'high' ? glassBlurFactor : 0
    fgMaterial.uniforms.u_enable_refraction.value = visualQuality === 'high' ? 1.0 : 0.0
    fgMaterial.uniforms.u_glass_taps.value = glassTaps
    fgMaterial.uniforms.u_audio_pulse.value = effects.beatPulse
    fgMaterial.uniforms.u_scroll_offset.value = scrollPosition

    const targetGradientColor = onAirColor || targetPlayerGradientColorRef.current
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

    const gyro = gyroscopeRef?.current || NO_PARALLAX
    const mouse = mouseRef?.current || NO_PARALLAX
    const hasGyro = Math.abs(gyro.parallaxX) > 0.001 || Math.abs(gyro.parallaxY) > 0.001
    const pX = hasGyro ? gyro.parallaxX * 40 : mouse.parallaxX * 40
    const pY = hasGyro ? gyro.parallaxY * 40 : mouse.parallaxY * 40

    if (transitionProgressRef.current < 1) {
        transitionProgressRef.current = Math.min(1, transitionProgressRef.current + delta / 0.7)
    }

    bgMaterial.uniforms.u_time.value = (bgMaterial.uniforms.u_time.value + delta) % 1000.0
    bgMaterial.uniforms.u_transition.value = transitionProgressRef.current
    if (reduceMotion) {
      bgMaterial.uniforms.u_parallax.value.set(0, 0)
      bgMaterial.uniforms.u_glitch.value.set(0, 0)
      bgMaterial.uniforms.u_frame_offset.value.set(0, 0)
      bgMaterial.uniforms.u_frame_scale.value = 1.0
      bgMaterial.uniforms.u_scale.value = 1.0
      bgMaterial.uniforms.u_rotation.value = 0.0
    } else {
      bgMaterial.uniforms.u_parallax.value.set(pX, pY)
      bgMaterial.uniforms.u_glitch.value.set(effects.glitchX, effects.glitchY)
      bgMaterial.uniforms.u_frame_offset.value.set(effects.frameOffset.x, effects.frameOffset.y)
      bgMaterial.uniforms.u_frame_scale.value = effects.frameScale
      bgMaterial.uniforms.u_scale.value = effects.scale || 1.0
      bgMaterial.uniforms.u_rotation.value = effects.rotation * Math.PI / 180
    }
    bgMaterial.uniforms.u_brightness.value = effects.brightness
    bgMaterial.uniforms.u_contrast.value = effects.contrast
    bgMaterial.uniforms.u_saturation.value = effects.saturation
    bgMaterial.uniforms.u_hue.value = effects.hue * Math.PI / 180
    bgMaterial.uniforms.u_max_blur.value = visualQuality === 'high' ? effects.blur : 0
    bgMaterial.uniforms.u_chromatic.value = visualQuality === 'high' && !reduceMotion ? effects.chromatic : 0
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
        renderSignatureRef.current.captureStale = true
    }

    if (textTexture) bgMaterial.uniforms.u_text_texture.value = textTexture
    bgMaterial.uniforms.u_text_empty.value = textTexture?.userData?.empty ? 1.0 : 0.0
    bgMaterial.uniforms.u_canvas_resolution.value.set(logicalWidth, logicalHeight)

    const glowUniforms = fgMaterial.uniforms
    const voiceLevel = glowUniforms.u_voice_level.value
    const onAirLevel = glowUniforms.u_on_air.value
    const crossfadeLevel = ambientRef.current.crossfade
    glowUniforms.u_glow_active.value = voiceLevel >= 0.001 || onAirLevel >= 0.001 || crossfadeLevel >= 0.001 ? 1.0 : 0.0
    const glowAnchorMix = Math.min(1, Math.max(0, glowUniforms.u_radio_button_state.value))
    const glowAnchor = glowUniforms.u_radio_button_pos.value
    glowUniforms.u_glow_center.value.set(
      0.5 + (Math.min(1, Math.max(0, glowAnchor.x)) - 0.5) * glowAnchorMix,
      0.55 + (Math.min(1, Math.max(0, glowAnchor.y)) - 0.55) * glowAnchorMix
    )
    glowUniforms.u_glow_scale.value.set(logicalWidth / Math.max(logicalHeight, 1) * GLOW_FALLOFF_SCALE, GLOW_FALLOFF_SCALE)
    glowUniforms.u_voice_glow.value.copy(glowUniforms.u_voice_color.value).multiplyScalar(voiceLevel * 0.45)
    glowUniforms.u_on_air_glow.value.copy(glowUniforms.u_on_air_color.value).multiplyScalar(onAirLevel)
      .addScaledVector(crossfadeColorRef.current, crossfadeLevel)
    const panelGlowMix = Math.min(1, Math.max(0, onAirLevel)) * 0.85
    const onAirTint = glowUniforms.u_on_air_color.value
    glowUniforms.u_panel_glow.value.set(
      0.6 + (onAirTint.x - 0.6) * panelGlowMix,
      0.7 + (onAirTint.y - 0.7) * panelGlowMix,
      0.9 + (onAirTint.z - 0.9) * panelGlowMix
    ).multiplyScalar(0.2 + 0.3 * onAirLevel)

    const bgUniforms = bgMaterial.uniforms
    const fgUniforms = fgMaterial.uniforms
    const signature = renderSignatureRef.current
    let sigLength = writeUniformSignature(signature.current, 0, bgUniforms, BG_SIGNATURE_UNIFORMS)
    signature.current[sigLength++] = Math.abs(effects.glitchX) > 20 ? bgUniforms.u_time.value : 0
    signature.current[sigLength++] = bgUniforms.u_parallax.value.x * PARALLAX_SIGNATURE_SCALE
    signature.current[sigLength++] = bgUniforms.u_parallax.value.y * PARALLAX_SIGNATURE_SCALE
    signature.current[sigLength++] = w
    signature.current[sigLength++] = h
    const captureLength = sigLength
    sigLength = writeUniformSignature(signature.current, sigLength, fgUniforms, FG_SIGNATURE_UNIFORMS)
    signature.current[sigLength++] = fgUniforms.u_radio_state_int.value === 4 ? fgUniforms.u_radio_time.value : 0

    const textures = signature.textures
    const textVersion = textTexture ? textTexture.version : -1
    const videoActive = bgUniforms.u_video_clip_blend.value > 0.01
    const wantsRender = signature.force ||
      videoActive ||
      hasVisiblePanels !== signature.fgVisible ||
      textures[0] !== bgUniforms.u_texture.value ||
      textures[1] !== bgUniforms.u_texture_prev.value ||
      textures[2] !== bgUniforms.u_text_texture.value ||
      textures[3] !== fgUniforms.u_noise_texture.value ||
      textVersion !== signature.textVersion ||
      sigLength !== signature.length ||
      signatureChanged(signature.current, signature.previous, sigLength)

    const captureDirty = signature.force || signature.captureStale || videoActive ||
      textures[0] !== bgUniforms.u_texture.value ||
      textures[1] !== bgUniforms.u_texture_prev.value ||
      textures[2] !== bgUniforms.u_text_texture.value ||
      textVersion !== signature.textVersion ||
      captureLength !== signature.captureLength ||
      signatureChanged(signature.current, signature.captured, captureLength)

    const paused = !signature.force && isSceneRenderingPaused(frameStart)
    reportFrame(delta, wantsRender && !paused)
    const frameSeconds = frameStart / 1000
    const throttled = paused || (fpsCap > 0 && !signature.force && frameSeconds - lastDrawAtRef.current < 1 / fpsCap - FRAME_CAP_SLACK_SECONDS)
    const needsRender = wantsRender && !throttled

    if (needsRender) {
      lastDrawAtRef.current = frameSeconds
      signature.force = false
      signature.fgVisible = hasVisiblePanels
      signature.textVersion = textVersion
      signature.length = sigLength
      textures[0] = bgUniforms.u_texture.value
      textures[1] = bgUniforms.u_texture_prev.value
      textures[2] = bgUniforms.u_text_texture.value
      textures[3] = fgUniforms.u_noise_texture.value
      const swap = signature.previous
      signature.previous = signature.current
      signature.current = swap

      if (hasVisiblePanels && captureDirty) {
        bgUniforms.u_canvas_resolution.value.set(rtWidth, rtHeight)
        bgUniforms.u_is_capture.value = 1.0
        gl.setRenderTarget(captureRenderTarget)
        gl.render(captureScene, captureCamera)
        const probeReader = probeReaderRef.current
        if (probeReader?.ready && frameStart - probeAtRef.current >= PROBE_INTERVAL_MS && lightProbeWanted(frameStart)) {
          probeAtRef.current = frameStart
          gl.setRenderTarget(probeTarget)
          gl.render(probeScene, captureCamera)
          probeReader.request()
        }
        gl.setRenderTarget(null)
        bgUniforms.u_is_capture.value = 0.0
        bgUniforms.u_canvas_resolution.value.set(logicalWidth, logicalHeight)
        signature.captureStale = false
        signature.captureLength = captureLength
        signature.captured.set(signature.previous.subarray(0, captureLength))
      } else if (!hasVisiblePanels) {
        signature.captureStale = true
      }

      if (glassMeshRef.current) glassMeshRef.current.visible = hasVisiblePanels
      if (backdropMeshRef.current) backdropMeshRef.current.visible = !hasVisiblePanels

      gl.render(scene, camera)
      splashReady('scene')
    }

    const frameEnd = performance.now()
    const frameTime = frameEnd - frameStart
    frameTimingRef.current.total += frameTime
    frameTimingRef.current.count++
    if (needsRender) frameTimingRef.current.rendered = (frameTimingRef.current.rendered || 0) + 1

    if (now - frameTimingRef.current.lastLog > 1000) {
      const avgFrameTime = frameTimingRef.current.total / frameTimingRef.current.count
      if (settingsState.fpsEnabled) {
        const debugInfo = {
          avgFrameTimeMs: avgFrameTime.toFixed(2),
          framesInSecond: frameTimingRef.current.count,
          framesRendered: frameTimingRef.current.rendered || 0,
          hasVisiblePanels,
          radioBtn: fgMaterial.uniforms.u_radio_button_state.value.toFixed(3),
          panelOpacities: panelOpacitiesLerpRef.current.map(o => o.toFixed(2)).join(','),
          captureRan: hasVisiblePanels && captureDirty ? 'YES' : 'no',
          videoClipsActive: clipState.textures.length > 0 && visualQuality === 'high',
          videoBlend: clipState.blend.toFixed(2),
          gyroActive: Math.abs(gyro.parallaxX) > 0.001 || Math.abs(gyro.parallaxY) > 0.001,
          gyroValues: `${gyro.parallaxX?.toFixed(3)},${gyro.parallaxY?.toFixed(3)}`,
          mouseValues: `${mouse.parallaxX?.toFixed(3)},${mouse.parallaxY?.toFixed(3)}`,
          visualQuality,
          dpr: gl.getPixelRatio().toFixed(2),
          fpsCap,
          glassTaps,
        }
        logger.debug('[SHADER PERF]', debugInfo)
      }
      frameTimingRef.current.total = 0
      frameTimingRef.current.count = 0
      frameTimingRef.current.rendered = 0
      frameTimingRef.current.lastLog = now
    }
  }, 1)

  return (
    <>
      <mesh ref={glassMeshRef} geometry={defaultGeometry} material={fgMaterial} />
      <mesh ref={backdropMeshRef} geometry={defaultGeometry} material={backdropMaterial} />
    </>
  )
}

function ContextLossMonitor({ onContextLostChange }) {
  const gl = useThree(state => state.gl)

  useEffect(() => {
    const canvas = gl.domElement
    const handleLost = () => {
      logger.warn('[AudioReactiveCanvas] WebGL context lost, showing fallback')
      onContextLostChange(true)
    }
    const handleRestored = () => {
      logger.info('[AudioReactiveCanvas] WebGL context restored')
      onContextLostChange(false)
    }
    canvas.addEventListener('webglcontextlost', handleLost)
    canvas.addEventListener('webglcontextrestored', handleRestored)
    return () => {
      canvas.removeEventListener('webglcontextlost', handleLost)
      canvas.removeEventListener('webglcontextrestored', handleRestored)
      onContextLostChange(false)
    }
  }, [gl, onContextLostChange])

  return null
}

const LyricsRenderer = memo(function LyricsRenderer({
  lyricDataRef,
  lyricCanvasRef,
  lyricTextureRef,
  lastWordRef,
}) {
  const {
    engineRef,
    engineState,
    isOfflineRendering,
    isScreenVisible,
  } = useUISelector(state => ({
    engineRef: state.engineRef,
    engineState: state.engineState,
    isOfflineRendering: state.isOfflineRendering,
    isScreenVisible: state.isScreenVisible,
  }))
  const intervalRef = useRef(null)
  const lastWordIndexRef = useRef(-1)

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

      const currentIndex = lyricData.findIndex(w =>
        currentTimeSec >= w.start && currentTimeSec <= w.end
      )

      if (currentIndex !== lastWordIndexRef.current) {
        lastWordIndexRef.current = currentIndex

        const canvas = lyricCanvasRef.current
        const ctx = canvas.getContext('2d')
        const texture = lyricTextureRef.current

        const textToDraw = currentIndex >= 0 ? lyricData[currentIndex].text : null

        renderLyricToCanvas(ctx, textToDraw, canvas.width, canvas.height)
        texture.userData.empty = !textToDraw
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

const AudioReactiveScene = memo(function AudioReactiveScene({
  captureResolution = 128,
  glassBlurFactor = 1.0,
  onContextLostChange,
}) {
  const currentArtwork = useThemeArtwork()
  const {
    audioFeatures,
    lyricTimestamps,
    engineState,
    isOfflineRendering,
    settingsState,
    isScreenVisible,
  } = useUISelector(state => ({
    audioFeatures: state.audioFeatures,
    lyricTimestamps: state.lyricTimestamps,
    engineState: state.engineState,
    isOfflineRendering: state.isOfflineRendering,
    settingsState: state.settingsState,
    isScreenVisible: state.isScreenVisible,
  }))
  const { sceneDpr } = useQuality()
  const visualQuality = settingsState.visualQuality || 'high'
  const deviceDpr = window.devicePixelRatio || 1
  const referenceDpr = Math.min(deviceDpr, visualQuality === 'high' ? REFERENCE_SCENE_DPR : 1.0)
  const canvasDpr = Math.min(referenceDpr, sceneDpr)

  const currentTrackId = engineState?.currentTrack?.id
  const videoClips = useVideoClips(currentTrackId)

  const [lyricResources] = useState(createLyricResources)
  const textTexture = lyricResources.texture
  const transitionProgressRef = useRef(1)

  const textRendererRef = useRef(null)
  const processedLyricDataRef = useRef(null)
  const lyricCanvasRef = useRef(null)
  const lyricTextureRef = useRef(null)
  const lastWordRef = useRef(null)

  const noiseTexture = useMemo(() => generateNoiseTexture(), [])

  useEffect(() => {
    lyricCanvasRef.current = lyricResources.canvas
    lyricTextureRef.current = lyricResources.texture
    return () => {
      lyricResources.texture.dispose()
      lyricCanvasRef.current = null
      lyricTextureRef.current = null
    }
  }, [lyricResources])

  useEffect(() => {
    lastWordRef.current = null
    const canvas = lyricCanvasRef.current
    if (canvas) {
      const ctx = canvas.getContext('2d')
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      if (lyricTextureRef.current) {
        lyricTextureRef.current.userData.empty = true
        lyricTextureRef.current.needsUpdate = true
      }
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
        gl={{ antialias: false, powerPreference: 'high-performance', stencil: false, depth: false, alpha: false }}
        dpr={canvasDpr}
        flat
        linear
        frameloop={isScreenVisible ? 'always' : 'never'}
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
          referenceDpr={referenceDpr}
        />
        <LyricsRenderer
          lyricDataRef={processedLyricDataRef}
          textRendererRef={textRendererRef}
          lyricCanvasRef={lyricCanvasRef}
          lyricTextureRef={lyricTextureRef}
          lastWordRef={lastWordRef}
        />
        <ContextLossMonitor onContextLostChange={onContextLostChange} />
      </Canvas>
    </>
  )
})

const VisualFallback = memo(function VisualFallback() {
  const currentArtwork = useThemeArtwork()

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
      {currentArtwork && (
        <div
          className="absolute -inset-16 bg-cover bg-center opacity-60"
          style={{ backgroundImage: `url(${currentArtwork})`, filter: 'blur(48px)' }}
        />
      )}
      <div className="absolute inset-0 bg-black/50" />
    </div>
  )
})

export const AudioReactiveCanvas = memo(function AudioReactiveCanvas(props) {
  const [webglAvailable] = useState(isWebGL2Available)
  const [contextLost, setContextLost] = useState(false)

  useEffect(() => {
    if (!webglAvailable) logger.warn('[AudioReactiveCanvas] WebGL2 unavailable, using static fallback')
  }, [webglAvailable])

  if (!webglAvailable) return <VisualFallback />

  return (
    <VisualErrorBoundary name="AudioReactiveCanvas" fallback={<VisualFallback />}>
      <AudioReactiveScene {...props} onContextLostChange={setContextLost} />
      {contextLost && <VisualFallback />}
    </VisualErrorBoundary>
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

  if (!trackingState.energyScratch) trackingState.energyScratch = []
  const energyThreshold = pushEnergySample(trackingState.energyHistory, trackingState.energyScratch, rawEnergy)
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