const ENERGY_WINDOW_MS = 1667

export const numericAscending = (a, b) => a - b

export function lowerBound(sorted, value) {
  let lo = 0
  let hi = sorted.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (sorted[mid] < value) lo = mid + 1
    else hi = mid
  }
  return lo
}

export function pushEnergySample(history, stamps, sorted, value, now) {
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
