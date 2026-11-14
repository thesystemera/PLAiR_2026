const NO_RADIO = ['if (adjustedDist < 1.0 && u_radio_button_radius.x > 0.001 && u_radio_button_state > 0.005) {', 'if (false) {']
const CONST_LOOP = ['if (i >= u_panel_count) break;', 'if (i >= 2) break;']
const interior = edits => ({ interior: { edits } })
export default {
  'int no noise': interior([['vec2 noiseSample = texture2D(u_noise_texture, worldSpaceUV).rg * 2.0 - 1.0;', 'vec2 noiseSample = vec2(0.1, -0.1);']]),
  'int one tap': interior([['panelColor = sampleBokehTexture(u_capture_texture, refractedScreenUV, blurRadius);', 'panelColor = texture2D(u_capture_texture, refractedScreenUV).rgb;']]),
  'int no radio': interior([NO_RADIO]),
  'int no refraction': interior([['if (totalDepth > 0.001 && u_enable_refraction > 0.5) {', 'if (false) {']]),
  'int no effects': interior([['vec3 processedPanel = applyPanelEffects(panelColor, screenUV, totalPanelMask, totalEdgeGlow, headerFactor, activePanelIndex, activePanelRegion);', 'vec3 processedPanel = panelColor * 0.3;']]),
  'int no loop': interior([['for(int i = 0; i < 7; i++) {', 'totalPanelMask = 1.0; totalDepth = 0.5; for(int i = 0; i < 0; i++) {']]),
  'int const loop': interior([CONST_LOOP]),
  'int const loop no radio': interior([CONST_LOOP, NO_RADIO]),
  'both no radio': { interior: { edits: [NO_RADIO] }, outer: { edits: [NO_RADIO] } },
  'both const loop no radio': { interior: { edits: [CONST_LOOP, NO_RADIO] }, outer: { edits: [CONST_LOOP, NO_RADIO] } },
}
