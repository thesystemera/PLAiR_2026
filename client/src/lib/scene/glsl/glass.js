export const GLASS_BODY = `
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

    #ifdef GLASS_RADIO_BUTTON
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
    #endif

    return finalColor;
  }
`
