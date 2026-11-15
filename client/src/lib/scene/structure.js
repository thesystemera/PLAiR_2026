import { lowerBound, numericAscending } from '../sceneEffects'

const STEP_S = 0.25
const SMOOTH_S = 1.5
const ACCENT_WINDOW_S = 0.07
const MIN_SECTION_S = 6
const SOLO_ENERGY = 0.55
const ROLES = {
  intro: 'intro',
  verse: 'verse',
  'pre-chorus': 'build',
  chorus: 'chorus',
  bridge: 'bridge',
  outro: 'outro',
  breakdown: 'break',
  instrumental: 'break',
}

function ranks(values) {
  const sorted = Array.from(values).sort(numericAscending)
  const last = Math.max(1, sorted.length - 1)
  return Float32Array.from(values, value => lowerBound(sorted, value) / last)
}

function loudnessCurve(segments, bins) {
  const sum = new Float32Array(bins)
  const count = new Float32Array(bins)
  for (const segment of segments) {
    const bin = Math.min(bins - 1, Math.floor(segment.start / STEP_S))
    sum[bin] += segment.loudness
    count[bin]++
  }
  const loud = new Float32Array(bins)
  let previous = -60
  for (let i = 0; i < bins; i++) {
    previous = count[i] ? sum[i] / count[i] : previous
    loud[i] = previous
  }
  const half = Math.round(SMOOTH_S / STEP_S / 2)
  return Float32Array.from(loud, (_, i) => {
    let total = 0
    let n = 0
    for (let j = Math.max(0, i - half); j <= Math.min(bins - 1, i + half); j++) {
      total += loud[j]
      n++
    }
    return total / n
  })
}

function beatAccents(beats, segments, smooth) {
  const starts = segments.map(segment => segment.start)
  const raw = beats.map(beat => {
    let peak = -120
    for (let i = Math.max(0, lowerBound(starts, beat - ACCENT_WINDOW_S)); i < segments.length && starts[i] <= beat + ACCENT_WINDOW_S; i++) {
      peak = Math.max(peak, segments[i].loudness_max ?? segments[i].loudness)
    }
    return peak - smooth[Math.min(smooth.length - 1, Math.floor(beat / STEP_S))]
  })
  return { raw, ranked: ranks(raw) }
}

function meterOf(features, raw) {
  const signature = Number(features.time_signature)
  const bpb = signature >= 2 && signature <= 7 ? signature : 4
  let phase = 0
  let best = -Infinity
  for (let p = 0; p < bpb; p++) {
    let total = 0
    let n = 0
    for (let b = p; b < raw.length; b += bpb) {
      total += raw[b]
      n++
    }
    if (n && total / n > best) {
      best = total / n
      phase = p
    }
  }
  return { bpb, phase }
}

function audioSections(features, duration, energyOf) {
  const starts = [0, ...(features.sections || []).map(section => section?.start).filter(start => Number.isFinite(start) && start > 0 && start < duration)]
    .sort(numericAscending)
  const merged = []
  for (const start of starts) {
    if (merged.length && start - merged[merged.length - 1] < MIN_SECTION_S) continue
    merged.push(start)
  }
  return merged.map((start, i) => {
    const end = merged[i + 1] ?? duration
    const energy = energyOf(start, end)
    let type = energy >= 0.66 ? 'chorus' : 'verse'
    if (i === 0 && energy < 0.5) type = 'intro'
    else if (i === merged.length - 1 && merged.length > 2 && energy < 0.5) type = 'outro'
    return { start, end, type, sung: true }
  })
}

export function analyzeSong(features, lyricSections) {
  const beats = features?.beats || []
  const segments = features?.loudness_segments || []
  const lastSegment = segments[segments.length - 1]
  const duration = features?.duration || (lastSegment ? lastSegment.start + lastSegment.duration : (beats[beats.length - 1] || 0) + 1)
  const bins = Math.max(1, Math.ceil(duration / STEP_S))
  const smooth = loudnessCurve(segments, bins)
  const energy = ranks(smooth)
  const energyOf = (start, end) => {
    const a = Math.max(0, Math.floor(start / STEP_S))
    const b = Math.min(bins, Math.max(a + 1, Math.ceil(end / STEP_S)))
    let total = 0
    for (let i = a; i < b; i++) total += energy[i]
    return total / (b - a)
  }
  const accents = beatAccents(beats, segments, smooth)
  const { bpb, phase } = meterOf(features || {}, accents.raw)
  const given = (lyricSections || []).filter(section => Number.isFinite(section?.start) && Number.isFinite(section?.end) && section.end > section.start)
  const base = given.length >= 2 ? given : audioSections(features || {}, duration, energyOf)
  let choruses = 0
  const sections = base.map(section => {
    const sectionEnergy = energyOf(section.start, section.end)
    let role = ROLES[section.type] || 'verse'
    if (role === 'break') role = sectionEnergy >= SOLO_ENERGY ? 'solo' : 'interlude'
    return { start: section.start, end: section.end, type: section.type, role, energy: sectionEnergy, sung: section.sung !== false, chorus: role === 'chorus' ? choruses++ : -1 }
  })
  for (const section of sections) section.final = section.role === 'chorus' && section.chorus === choruses - 1 && choruses > 1
  const period = beats.length > 1 ? (beats[beats.length - 1] - beats[0]) / (beats.length - 1) : 0.5
  return { beats, bpb, phase, period, duration, step: STEP_S, energy, accents: accents.ranked, sections }
}

export function energyAt(song, t) {
  return song.energy[Math.max(0, Math.min(song.energy.length - 1, Math.floor(t / song.step)))]
}

export function isDownbeat(song, beat) {
  return (((beat - song.phase) % song.bpb) + song.bpb) % song.bpb === 0
}
