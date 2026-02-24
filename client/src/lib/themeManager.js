import { logger } from './logger'

function rgbToCss({ r, g, b }, opacity = 1) {
  return `rgba(${r}, ${g}, ${b}, ${opacity})`
}

export async function extractColorsFromImage(imageUrl) {
  return new Promise((resolve, _reject) => {
    const img = new Image()
    img.crossOrigin = 'Anonymous'

    img.onload = () => {
      const canvas = document.createElement('canvas')
      const ctx = canvas.getContext('2d')

      const size = 100
      canvas.width = size
      canvas.height = size

      ctx.drawImage(img, 0, 0, size, size)

      try {
        const imageData = ctx.getImageData(0, 0, size, size)
        const colors = analyzeColors(imageData.data)
        resolve(colors)
      } catch (error) {
        logger.error('Failed to extract colors:', error)
        resolve(getDefaultColors())
      }
    }

    img.onerror = () => {
      logger.error('Failed to load image for color extraction')
      resolve(getDefaultColors())
    }

    img.src = imageUrl
  })
}

function buildHistogram(data) {
  const colorCounts = {}
  const bucketSize = 16

  for (let i = 0; i < data.length; i += 4) {
    const r = Math.floor(data[i] / bucketSize) * bucketSize
    const g = Math.floor(data[i + 1] / bucketSize) * bucketSize
    const b = Math.floor(data[i + 2] / bucketSize) * bucketSize
    const a = data[i + 3]

    if (a < 125) continue

    const key = `${r},${g},${b}`
    colorCounts[key] = (colorCounts[key] || 0) + 1
  }

  return Object.entries(colorCounts)
    .map(([color, count]) => {
      const [r, g, b] = color.split(',').map(Number)
      return { r, g, b, count, lum: getLuminance({ r, g, b }), sat: getSaturation({ r, g, b }) }
    })
    .sort((a, b) => b.count - a.count)
}

function extractFunctionalPalette(allColors) {
  if (allColors.length === 0) {
    return {
      accent: { r: 139, g: 92, b: 246 },
      muted: { r: 100, g: 80, b: 180 },
      dark: { r: 30, g: 25, b: 50 },
      vibrant: { r: 139, g: 92, b: 246 },
      dominant: { r: 139, g: 92, b: 246 }
    }
  }

  const dominant = allColors[0]

  const brightBin = allColors.filter(c => c.lum >= 0.4 && c.lum <= 0.75)
  const midBin = allColors.filter(c => c.lum >= 0.2 && c.lum < 0.4)
  const darkBin = allColors.filter(c => c.lum >= 0.05 && c.lum < 0.2)

  const vibrantCandidates = allColors.filter(c => c.sat > 0.3)
  const vibrant = pickBestFromBin(vibrantCandidates, 'saturation') || pickBestFromBin(allColors, 'saturation') || dominant

  let accent = pickBestFromBin(brightBin, 'balanced')

  if (!accent) {
    const midVibrant = midBin.filter(c => c.sat > 0.25)
    if (midVibrant.length > 0) {
      accent = boostToBrightness(pickBestFromBin(midVibrant, 'saturation'), 0.5)
    } else if (midBin.length > 0) {
      accent = boostToBrightness(pickBestFromBin(midBin, 'balanced'), 0.5)
    } else {
      accent = boostToBrightness(dominant, 0.55)
    }
  }

  const accentLum = getLuminance(accent)
  if (accentLum < 0.4) {
    accent = boostToBrightness(accent, 0.5)
  }

  let muted = pickBestFromBin(midBin, 'balanced')
  if (!muted) {
    muted = dimToBrightness(accent, 0.3)
  }

  let dark = pickBestFromBin(darkBin, 'balanced')
  if (!dark) {
    dark = dimToBrightness(muted || accent, 0.12)
  }

  return { accent, muted, dark, vibrant, dominant }
}

function pickBestFromBin(colors, strategy) {
  if (!colors || colors.length === 0) return null

  const candidates = colors.slice(0, 15)

  if (strategy === 'saturation') {
    let best = candidates[0]
    let bestScore = 0
    for (const c of candidates) {
      const score = c.sat * (1 + Math.log(c.count + 1) * 0.1)
      if (score > bestScore) {
        bestScore = score
        best = c
      }
    }
    return best
  }

  if (strategy === 'balanced') {
    let best = candidates[0]
    let bestScore = 0
    for (const c of candidates) {
      const satBonus = c.sat > 0.25 ? 1.5 : 1
      const score = Math.log(c.count + 1) * satBonus * (0.5 + c.sat)
      if (score > bestScore) {
        bestScore = score
        best = c
      }
    }
    return best
  }

  return candidates[0]
}

function boostToBrightness(color, targetLum) {
  const currentLum = getLuminance(color)
  if (currentLum >= targetLum) return color

  const hsl = rgbToHsl(color)
  const newL = Math.min(0.75, Math.max(targetLum, hsl.l + (targetLum - currentLum) * 1.5))
  const newS = Math.min(1, hsl.s * 1.1)

  return hslToRgb(hsl.h, newS, newL)
}

function dimToBrightness(color, targetLum) {
  const hsl = rgbToHsl(color)
  const newL = Math.max(0.05, Math.min(targetLum, hsl.l * 0.5))
  return hslToRgb(hsl.h, hsl.s * 0.8, newL)
}

function analyzeColors(data) {
  const allColors = buildHistogram(data)

  if (allColors.length === 0) {
    return getDefaultColors()
  }

  const palette = extractFunctionalPalette(allColors)

  const dominantHsl = rgbToHsl(palette.dominant)
  const accentHsl = rgbToHsl(palette.accent)

  const hue = (dominantHsl.h * 0.6 + accentHsl.h * 0.4)
  const saturation = Math.min(0.15, dominantHsl.s * 0.2)

  const avgLuminance = (getLuminance(palette.accent) + getLuminance(palette.muted)) / 2
  const isDarkArtwork = avgLuminance < 0.35

  const textLightness = isDarkArtwork ? 0.65 : 0.55
  const textLighterLightness = isDarkArtwork ? 0.85 : 0.75

  const colors = {
    primaryRgb: palette.accent,
    secondaryRgb: palette.muted,
    accentRgb: palette.accent,

    appBackgroundRgb: hslToRgb(hue, saturation, 0.08),
    cardBackgroundRgb: hslToRgb(hue, saturation, 0.12),
    cardHoverBackgroundRgb: hslToRgb(hue, saturation, 0.18),
    panelBackgroundRgb: hslToRgb(hue, saturation, 0.15),

    primaryTextRgb: hslToRgb(hue, Math.min(0.05, saturation * 0.3), 0.98),
    secondaryTextRgb: hslToRgb(hue, Math.min(0.1, saturation * 0.6), textLightness),
    mutedTextRgb: hslToRgb(hue, Math.min(0.08, saturation * 0.5), textLighterLightness),
    tertiaryTextRgb: hslToRgb(hue, Math.min(0.08, saturation * 0.5), 0.50),

    borderColorRgb: hslToRgb(hue, saturation, 0.3),
    subtleBorderRgb: hslToRgb(hue, saturation, 0.2),
    activeBorderRgb: palette.accent,
    panelBorderRgb: hslToRgb(hue, saturation, 0.15),
    inputBorderRgb: hslToRgb(hue, saturation, 0.2),

    buttonHoverBgRgb: hslToRgb(hue, saturation, 0.18),
    buttonActiveBgRgb: blendColors(palette.vibrant, hslToRgb(258, 0.6, 0.55), 0.4),
    buttonInactiveBgRgb: hslToRgb(hue, saturation, 0.18),
    inputBackgroundRgb: hslToRgb(hue, saturation, 0.18),
    overlayBackgroundRgb: hslToRgb(hue, Math.min(0.05, saturation * 0.3), 0.98),

    playingBackgroundRgb: dimToBrightness(palette.vibrant, 0.15),
    playingRingRgb: palette.vibrant,
    queuedRingRgb: hslToRgb(210, 0.7, 0.55),
    loadingSpinnerRgb: blendColors(palette.accent, hslToRgb(258, 0.7, 0.62), 0.5),

    purpleBaseRgb: blendColors(palette.accent, hslToRgb(258, 0.7, 0.62), 0.3),
    purpleDarkRgb: blendColors(palette.muted, hslToRgb(258, 0.7, 0.45), 0.3),
    purpleLightRgb: blendColors(palette.accent, hslToRgb(258, 0.6, 0.75), 0.3),

    radioButtonBaseRgb: hslToRgb(140 * 0.7 + hue * 0.3, 0.7, 0.55),

    primaryActionTextRgb: blendColors(palette.accent, hslToRgb(258, 0.6, 0.65), 0.4),
    primaryActionHoverRgb: boostToBrightness(palette.accent, 0.7),
    dangerActionTextRgb: hslToRgb(0, 0.65, 0.60),
    dangerActionBgRgb: hslToRgb(0, 0.5, 0.50),

    successColorRgb: hslToRgb(140, 0.6, 0.45),
    errorColorRgb: hslToRgb(0, 0.7, 0.50),
    warningColorRgb: hslToRgb(45, 0.8, 0.55),
    infoColorRgb: hslToRgb(210, 0.7, 0.55),

    networkExcellentRgb: { r: 34, g: 197, b: 94 },
    networkGoodRgb: { r: 59, g: 130, b: 246 },
    networkFairRgb: { r: 234, g: 179, b: 8 },
    networkPoorRgb: { r: 239, g: 68, b: 68 },

    likeBadgeBgRgb: { r: 219, g: 39, b: 119 },
    superLikeBadgeBgRgb: { r: 202, g: 138, b: 4 },
    banBadgeBgRgb: { r: 220, g: 38, b: 38 },

    filterAllActiveRgb: {
      bg: blendColors(palette.accent, hslToRgb(258, 0.5, 0.55), 0.4),
      text: boostToBrightness(palette.accent, 0.75),
      border: blendColors(palette.accent, hslToRgb(258, 0.5, 0.55), 0.4)
    },
    filterInteractiveActiveRgb: {
      bg: hslToRgb(210, 0.5, 0.55),
      text: hslToRgb(210, 0.5, 0.75),
      border: hslToRgb(210, 0.5, 0.55)
    },
    filterAnnouncerActiveRgb: {
      bg: hslToRgb(45, 0.5, 0.55),
      text: hslToRgb(45, 0.5, 0.75),
      border: hslToRgb(45, 0.5, 0.55)
    },
    filterExternalActiveRgb: {
      bg: hslToRgb(140, 0.5, 0.55),
      text: hslToRgb(140, 0.5, 0.75),
      border: hslToRgb(140, 0.5, 0.55)
    },
    filterShoutoutsActiveRgb: {
      bg: hslToRgb(330, 0.5, 0.55),
      text: hslToRgb(330, 0.5, 0.75),
      border: hslToRgb(330, 0.5, 0.55)
    },
    filterInactiveRgb: {
      bg: hslToRgb(hue, saturation * 0.8, 0.25),
      text: hslToRgb(hue, Math.min(0.1, saturation * 0.6), 0.55),
      border: hslToRgb(hue, saturation * 0.8, 0.25)
    },

    userAvatarGradientRgb: {
      from: blendColors(palette.vibrant, hslToRgb(258, 0.7, 0.60), 0.4),
      to: hslToRgb(210, 0.7, 0.60)
    },
    premiumGradientRgb: {
      from: { r: 234, g: 179, b: 8 },
      to: { r: 249, g: 115, b: 22 }
    }
  }

  return {
    palette: [palette.accent, palette.vibrant, palette.muted, palette.dark, palette.dominant]
      .filter(Boolean)
      .slice(0, 5)
      .map(c => rgbToHex(c)),

    accent: rgbToCss(colors.accentRgb),
    gradient: `linear-gradient(135deg, ${rgbToCss(colors.primaryRgb, 0.3)}, ${rgbToCss(colors.secondaryRgb, 0.3)})`,
    gradientFull: `linear-gradient(135deg, ${rgbToCss(colors.primaryRgb)}, ${rgbToCss(colors.secondaryRgb)})`,

    appBackground: rgbToCss(colors.appBackgroundRgb),
    cardBackground: rgbToCss(colors.cardBackgroundRgb),
    cardHoverBackground: rgbToCss(colors.cardHoverBackgroundRgb),
    panelBackground: rgbToCss(colors.panelBackgroundRgb),

    primaryText: rgbToCss(colors.primaryTextRgb),
    secondaryText: rgbToCss(colors.secondaryTextRgb),
    mutedText: rgbToCss(colors.mutedTextRgb),
    tertiaryText: rgbToCss(colors.tertiaryTextRgb),

    border: rgbToCss(colors.borderColorRgb),
    subtleBorder: rgbToCss(colors.subtleBorderRgb),
    activeBorder: rgbToCss(colors.activeBorderRgb),
    panelBorder: rgbToCss(colors.panelBorderRgb),
    inputBorder: rgbToCss(colors.inputBorderRgb),

    buttonHoverBg: rgbToCss(colors.buttonHoverBgRgb),
    buttonActiveBg: rgbToCss(colors.buttonActiveBgRgb),
    buttonInactiveBg: rgbToCss(colors.buttonInactiveBgRgb),
    inputBackground: rgbToCss(colors.inputBackgroundRgb),
    overlayBackground: rgbToCss(colors.overlayBackgroundRgb),

    playingBackground: rgbToCss(colors.playingBackgroundRgb),
    playingRing: rgbToCss(colors.playingRingRgb),
    queuedRing: rgbToCss(colors.queuedRingRgb),
    loadingSpinner: rgbToCss(colors.loadingSpinnerRgb),

    purpleBase: rgbToCss(colors.purpleBaseRgb),
    purpleDark: rgbToCss(colors.purpleDarkRgb),
    purpleLight: rgbToCss(colors.purpleLightRgb),

    radioButtonBase: rgbToCss(colors.radioButtonBaseRgb),

    primaryActionText: rgbToCss(colors.primaryActionTextRgb),
    primaryActionHover: rgbToCss(colors.primaryActionHoverRgb),
    dangerActionText: rgbToCss(colors.dangerActionTextRgb),
    dangerActionBg: rgbToCss(colors.dangerActionBgRgb),

    success: rgbToCss(colors.successColorRgb),
    error: rgbToCss(colors.errorColorRgb),
    warning: rgbToCss(colors.warningColorRgb),
    info: rgbToCss(colors.infoColorRgb),

    networkExcellent: rgbToCss(colors.networkExcellentRgb),
    networkGood: rgbToCss(colors.networkGoodRgb),
    networkFair: rgbToCss(colors.networkFairRgb),
    networkPoor: rgbToCss(colors.networkPoorRgb),

    likeBadgeBg: rgbToCss(colors.likeBadgeBgRgb),
    superLikeBadgeBg: rgbToCss(colors.superLikeBadgeBgRgb),
    banBadgeBg: rgbToCss(colors.banBadgeBgRgb),

    filterAllActive: {
      background: rgbToCss(colors.filterAllActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterAllActiveRgb.text),
      borderColor: rgbToCss(colors.filterAllActiveRgb.border, 0.5)
    },
    filterInteractiveActive: {
      background: rgbToCss(colors.filterInteractiveActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterInteractiveActiveRgb.text),
      borderColor: rgbToCss(colors.filterInteractiveActiveRgb.border, 0.5)
    },
    filterAnnouncerActive: {
      background: rgbToCss(colors.filterAnnouncerActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterAnnouncerActiveRgb.text),
      borderColor: rgbToCss(colors.filterAnnouncerActiveRgb.border, 0.5)
    },
    filterExternalActive: {
      background: rgbToCss(colors.filterExternalActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterExternalActiveRgb.text),
      borderColor: rgbToCss(colors.filterExternalActiveRgb.border, 0.5)
    },
    filterShoutoutsActive: {
      background: rgbToCss(colors.filterShoutoutsActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterShoutoutsActiveRgb.text),
      borderColor: rgbToCss(colors.filterShoutoutsActiveRgb.border, 0.5)
    },
    filterInactive: {
      background: rgbToCss(colors.filterInactiveRgb.bg, 0.3),
      color: rgbToCss(colors.filterInactiveRgb.text),
      borderColor: rgbToCss(colors.filterInactiveRgb.border, 0.5)
    },

    userAvatarGradient: `linear-gradient(to bottom right, ${rgbToCss(colors.userAvatarGradientRgb.from)}, ${rgbToCss(colors.userAvatarGradientRgb.to)})`,
    premiumGradient: `linear-gradient(to right, ${rgbToCss(colors.premiumGradientRgb.from)}, ${rgbToCss(colors.premiumGradientRgb.to)})`,

    _rgb: colors
  }
}

function blendColors(c1, c2, t) {
  return {
    r: Math.round(c1.r * (1 - t) + c2.r * t),
    g: Math.round(c1.g * (1 - t) + c2.g * t),
    b: Math.round(c1.b * (1 - t) + c2.b * t)
  }
}

function getLuminance({ r, g, b }) {
  const rsRGB = r / 255
  const gsRGB = g / 255
  const bsRGB = b / 255

  const rLinear = rsRGB <= 0.03928 ? rsRGB / 12.92 : Math.pow((rsRGB + 0.055) / 1.055, 2.4)
  const gLinear = gsRGB <= 0.03928 ? gsRGB / 12.92 : Math.pow((gsRGB + 0.055) / 1.055, 2.4)
  const bLinear = bsRGB <= 0.03928 ? bsRGB / 12.92 : Math.pow((bsRGB + 0.055) / 1.055, 2.4)

  return 0.2126 * rLinear + 0.7152 * gLinear + 0.0722 * bLinear
}

function getSaturation({ r, g, b }) {
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  return max === 0 ? 0 : (max - min) / max
}

function rgbToHex({ r, g, b }) {
  return '#' + [r, g, b].map(x => {
    const hex = x.toString(16)
    return hex.length === 1 ? '0' + hex : hex
  }).join('')
}

function rgbToHsl({ r, g, b }) {
  r /= 255
  g /= 255
  b /= 255

  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const diff = max - min
  const l = (max + min) / 2

  if (diff === 0) {
    return { h: 0, s: 0, l }
  }

  const s = l > 0.5 ? diff / (2 - max - min) : diff / (max + min)

  let h
  switch (max) {
    case r:
      h = ((g - b) / diff + (g < b ? 6 : 0)) / 6
      break
    case g:
      h = ((b - r) / diff + 2) / 6
      break
    case b:
      h = ((r - g) / diff + 4) / 6
      break
  }

  return { h: h * 360, s, l }
}

function hslToRgb(h, s, l) {
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l - c / 2

  let r = 0, g = 0, b = 0
  if (h >= 0 && h < 60) { r = c; g = x; b = 0 }
  else if (h >= 60 && h < 120) { r = x; g = c; b = 0 }
  else if (h >= 120 && h < 180) { r = 0; g = c; b = x }
  else if (h >= 180 && h < 240) { r = 0; g = x; b = c }
  else if (h >= 240 && h < 300) { r = x; g = 0; b = c }
  else if (h >= 300 && h < 360) { r = c; g = 0; b = x }

  return {
    r: Math.round((r + m) * 255),
    g: Math.round((g + m) * 255),
    b: Math.round((b + m) * 255)
  }
}

function getDefaultColors() {
  const hue = 258
  const saturation = 0.1
  const primary = { r: 139, g: 92, b: 246 }
  const secondary = { r: 59, g: 130, b: 246 }

  const colors = {
    primaryRgb: primary,
    secondaryRgb: secondary,
    accentRgb: primary,

    appBackgroundRgb: hslToRgb(hue, saturation, 0.08),
    cardBackgroundRgb: hslToRgb(hue, saturation, 0.12),
    cardHoverBackgroundRgb: hslToRgb(hue, saturation, 0.18),
    panelBackgroundRgb: hslToRgb(hue, saturation, 0.15),

    primaryTextRgb: hslToRgb(hue, 0.05, 0.98),
    secondaryTextRgb: hslToRgb(hue, 0.1, 0.55),
    mutedTextRgb: hslToRgb(hue, 0.08, 0.75),
    tertiaryTextRgb: hslToRgb(hue, 0.08, 0.50),

    borderColorRgb: hslToRgb(hue, saturation, 0.3),
    subtleBorderRgb: hslToRgb(hue, saturation, 0.2),
    activeBorderRgb: primary,
    panelBorderRgb: hslToRgb(hue, saturation, 0.15),
    inputBorderRgb: hslToRgb(hue, saturation, 0.2),

    buttonHoverBgRgb: hslToRgb(hue, saturation, 0.18),
    buttonActiveBgRgb: hslToRgb(258, 0.6, 0.55),
    buttonInactiveBgRgb: hslToRgb(hue, saturation, 0.18),
    inputBackgroundRgb: hslToRgb(hue, saturation, 0.18),
    overlayBackgroundRgb: hslToRgb(hue, 0.05, 0.98),

    playingBackgroundRgb: hslToRgb(258, 0.5, 0.15),
    playingRingRgb: primary,
    queuedRingRgb: hslToRgb(210, 0.7, 0.55),
    loadingSpinnerRgb: hslToRgb(258, 0.7, 0.62),

    purpleBaseRgb: hslToRgb(258, 0.7, 0.62),
    purpleDarkRgb: hslToRgb(258, 0.7, 0.45),
    purpleLightRgb: hslToRgb(258, 0.6, 0.75),

    radioButtonBaseRgb: hslToRgb(140, 0.7, 0.55),

    primaryActionTextRgb: hslToRgb(258, 0.6, 0.65),
    primaryActionHoverRgb: hslToRgb(258, 0.5, 0.75),
    dangerActionTextRgb: hslToRgb(0, 0.65, 0.60),
    dangerActionBgRgb: hslToRgb(0, 0.5, 0.50),

    successColorRgb: hslToRgb(140, 0.6, 0.45),
    errorColorRgb: hslToRgb(0, 0.7, 0.50),
    warningColorRgb: hslToRgb(45, 0.8, 0.55),
    infoColorRgb: hslToRgb(210, 0.7, 0.55),

    networkExcellentRgb: { r: 34, g: 197, b: 94 },
    networkGoodRgb: { r: 59, g: 130, b: 246 },
    networkFairRgb: { r: 234, g: 179, b: 8 },
    networkPoorRgb: { r: 239, g: 68, b: 68 },

    likeBadgeBgRgb: { r: 219, g: 39, b: 119 },
    superLikeBadgeBgRgb: { r: 202, g: 138, b: 4 },
    banBadgeBgRgb: { r: 220, g: 38, b: 38 },

    filterAllActiveRgb: {
      bg: hslToRgb(258, 0.5, 0.55),
      text: hslToRgb(258, 0.5, 0.75),
      border: hslToRgb(258, 0.5, 0.55)
    },
    filterInteractiveActiveRgb: {
      bg: hslToRgb(210, 0.5, 0.55),
      text: hslToRgb(210, 0.5, 0.75),
      border: hslToRgb(210, 0.5, 0.55)
    },
    filterAnnouncerActiveRgb: {
      bg: hslToRgb(45, 0.5, 0.55),
      text: hslToRgb(45, 0.5, 0.75),
      border: hslToRgb(45, 0.5, 0.55)
    },
    filterExternalActiveRgb: {
      bg: hslToRgb(140, 0.5, 0.55),
      text: hslToRgb(140, 0.5, 0.75),
      border: hslToRgb(140, 0.5, 0.55)
    },
    filterShoutoutsActiveRgb: {
      bg: hslToRgb(330, 0.5, 0.55),
      text: hslToRgb(330, 0.5, 0.75),
      border: hslToRgb(330, 0.5, 0.55)
    },
    filterInactiveRgb: {
      bg: hslToRgb(hue, saturation * 0.8, 0.25),
      text: hslToRgb(hue, 0.1, 0.55),
      border: hslToRgb(hue, saturation * 0.8, 0.25)
    },

    userAvatarGradientRgb: {
      from: hslToRgb(258, 0.7, 0.60),
      to: hslToRgb(210, 0.7, 0.60)
    },
    premiumGradientRgb: {
      from: { r: 234, g: 179, b: 8 },
      to: { r: 249, g: 115, b: 22 }
    }
  }

  return {
    palette: ['#8b5cf6', '#3b82f6', '#6366f1', '#a855f7', '#7c3aed'],

    accent: rgbToCss(colors.accentRgb),
    gradient: `linear-gradient(135deg, ${rgbToCss(colors.primaryRgb, 0.3)}, ${rgbToCss(colors.secondaryRgb, 0.3)})`,
    gradientFull: `linear-gradient(135deg, ${rgbToCss(colors.primaryRgb)}, ${rgbToCss(colors.secondaryRgb)})`,

    appBackground: rgbToCss(colors.appBackgroundRgb),
    cardBackground: rgbToCss(colors.cardBackgroundRgb),
    cardHoverBackground: rgbToCss(colors.cardHoverBackgroundRgb),
    panelBackground: rgbToCss(colors.panelBackgroundRgb),

    primaryText: rgbToCss(colors.primaryTextRgb),
    secondaryText: rgbToCss(colors.secondaryTextRgb),
    mutedText: rgbToCss(colors.mutedTextRgb),
    tertiaryText: rgbToCss(colors.tertiaryTextRgb),

    border: rgbToCss(colors.borderColorRgb),
    subtleBorder: rgbToCss(colors.subtleBorderRgb),
    activeBorder: rgbToCss(colors.activeBorderRgb),
    panelBorder: rgbToCss(colors.panelBorderRgb),
    inputBorder: rgbToCss(colors.inputBorderRgb),

    buttonHoverBg: rgbToCss(colors.buttonHoverBgRgb),
    buttonActiveBg: rgbToCss(colors.buttonActiveBgRgb),
    buttonInactiveBg: rgbToCss(colors.buttonInactiveBgRgb),
    inputBackground: rgbToCss(colors.inputBackgroundRgb),
    overlayBackground: rgbToCss(colors.overlayBackgroundRgb),

    playingBackground: rgbToCss(colors.playingBackgroundRgb),
    playingRing: rgbToCss(colors.playingRingRgb),
    queuedRing: rgbToCss(colors.queuedRingRgb),
    loadingSpinner: rgbToCss(colors.loadingSpinnerRgb),

    purpleBase: rgbToCss(colors.purpleBaseRgb),
    purpleDark: rgbToCss(colors.purpleDarkRgb),
    purpleLight: rgbToCss(colors.purpleLightRgb),

    radioButtonBase: rgbToCss(colors.radioButtonBaseRgb),

    primaryActionText: rgbToCss(colors.primaryActionTextRgb),
    primaryActionHover: rgbToCss(colors.primaryActionHoverRgb),
    dangerActionText: rgbToCss(colors.dangerActionTextRgb),
    dangerActionBg: rgbToCss(colors.dangerActionBgRgb),

    success: rgbToCss(colors.successColorRgb),
    error: rgbToCss(colors.errorColorRgb),
    warning: rgbToCss(colors.warningColorRgb),
    info: rgbToCss(colors.infoColorRgb),

    networkExcellent: rgbToCss(colors.networkExcellentRgb),
    networkGood: rgbToCss(colors.networkGoodRgb),
    networkFair: rgbToCss(colors.networkFairRgb),
    networkPoor: rgbToCss(colors.networkPoorRgb),

    likeBadgeBg: rgbToCss(colors.likeBadgeBgRgb),
    superLikeBadgeBg: rgbToCss(colors.superLikeBadgeBgRgb),
    banBadgeBg: rgbToCss(colors.banBadgeBgRgb),

    filterAllActive: {
      background: rgbToCss(colors.filterAllActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterAllActiveRgb.text),
      borderColor: rgbToCss(colors.filterAllActiveRgb.border, 0.5)
    },
    filterInteractiveActive: {
      background: rgbToCss(colors.filterInteractiveActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterInteractiveActiveRgb.text),
      borderColor: rgbToCss(colors.filterInteractiveActiveRgb.border, 0.5)
    },
    filterAnnouncerActive: {
      background: rgbToCss(colors.filterAnnouncerActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterAnnouncerActiveRgb.text),
      borderColor: rgbToCss(colors.filterAnnouncerActiveRgb.border, 0.5)
    },
    filterExternalActive: {
      background: rgbToCss(colors.filterExternalActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterExternalActiveRgb.text),
      borderColor: rgbToCss(colors.filterExternalActiveRgb.border, 0.5)
    },
    filterShoutoutsActive: {
      background: rgbToCss(colors.filterShoutoutsActiveRgb.bg, 0.2),
      color: rgbToCss(colors.filterShoutoutsActiveRgb.text),
      borderColor: rgbToCss(colors.filterShoutoutsActiveRgb.border, 0.5)
    },
    filterInactive: {
      background: rgbToCss(colors.filterInactiveRgb.bg, 0.3),
      color: rgbToCss(colors.filterInactiveRgb.text),
      borderColor: rgbToCss(colors.filterInactiveRgb.border, 0.5)
    },

    userAvatarGradient: `linear-gradient(to bottom right, ${rgbToCss(colors.userAvatarGradientRgb.from)}, ${rgbToCss(colors.userAvatarGradientRgb.to)})`,
    premiumGradient: `linear-gradient(to right, ${rgbToCss(colors.premiumGradientRgb.from)}, ${rgbToCss(colors.premiumGradientRgb.to)})`,

    _rgb: colors
  }
}

export const SPACING = {
  panelPaddingX: { mobile: '0.75rem', desktop: '1.5rem' },
  panelPaddingY: { mobile: '0.75rem', desktop: '1.5rem' },
  panelPaddingBottom: { mobile: '0.75rem', desktop: '0.75rem' },
  gridGap: { mobile: '0.75rem', desktop: '1rem' },
  cardPadding: { mobile: '0.5rem', desktop: '1rem' }
}

export const PANEL = {
  headerHeight: 72
}

export const PANEL_SCROLL = {
  maskFadeTop: '16px',
  maskFadeBottom: '12px',
  maskFadeSide: '8px'
}

export const TRANSITIONS = {
  panel: { type: 'spring', stiffness: 300, damping: 30 },
  fade: { duration: 0.5, ease: 'easeInOut' },
  colorShift: { duration: 700 }
}

export const ANIMATION = {
  springStiffness: 300,
  springDamping: 30,
  fadeInDuration: 500,
  colorTransitionMs: 700
}

export const UI_FULLSCREEN = {
  autoHideDelay: 2000,
  fadeDuration: 0.5,
  radioGlassOpacity: 0.35
}

export const BUTTON = {
  overlay: {
    base: 'bg-black/60 hover:bg-black/80',
    padding: { small: 'p-2.5', medium: 'p-3' },
    rounded: 'rounded-full',
    shadow: 'shadow-lg',
    transition: 'transition-all hover:scale-110',
    disabled: 'disabled:opacity-50 disabled:cursor-not-allowed'
  },
  icon: {
    small: 'h-5 w-5',
    medium: 'h-6 w-6'
  },
  card: {
    play: 'p-3 md:p-4 bg-white text-black rounded-full shadow-lg',
    addToQueue: 'p-1.5 bg-white/20 hover:bg-white/30 text-white rounded-full transition-all',
    playOverlay: 'absolute inset-0 bg-black/70 opacity-0 group-hover/card:opacity-100 transition-opacity flex items-center justify-center z-10',
    durationBar: 'absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-2 flex items-center justify-between z-20'
  }
}

export const CATALOG_HEADER = {
  gradient: 'linear-gradient(to bottom, rgba(0, 0, 0, 0.98) 0%, rgba(0, 0, 0, 0.95) 60%, rgba(0, 0, 0, 0.7) 85%, rgba(0, 0, 0, 0.3) 95%, transparent 100%)',
  statIcon: {
    base: 'p-1.5 md:p-2 rounded-lg',
    size: 'w-3 h-3 md:w-4 md:h-4',
    music: 'bg-purple-500/20 text-purple-400',
    clock: 'bg-blue-500/20 text-blue-400'
  },
  statText: {
    value: 'text-lg md:text-xl font-bold tabular-nums leading-none',
    label: 'text-xs text-white/60 uppercase tracking-wide font-medium',
    detail: 'text-xs text-white/50',
    range: 'text-xs text-white/40'
  },
  sortButton: {
    active: 'px-2.5 py-1 text-xs md:text-sm rounded-full transition-all bg-purple-600 text-white font-semibold',
    inactive: 'px-2.5 py-1 text-xs md:text-sm rounded-full transition-all bg-dark-card hover:bg-dark-hover'
  }
}

export const FALLBACK_GRADIENTS = [
  'from-purple-600 to-blue-600',
  'from-pink-600 to-purple-600',
  'from-blue-600 to-cyan-600',
  'from-green-600 to-teal-600',
  'from-orange-600 to-red-600',
]

export function getFallbackGradientClass(trackId) {
  if (!trackId) return FALLBACK_GRADIENTS[0]
  try {
    const index = parseInt(trackId.slice(0, 2), 16) % FALLBACK_GRADIENTS.length
    return FALLBACK_GRADIENTS[index]
  } catch (e) {
    return FALLBACK_GRADIENTS[0]
  }
}