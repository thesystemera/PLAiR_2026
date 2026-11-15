import { lowerBound, numericAscending } from '../sceneEffects'

const STEP_S = 0.25
const SMOOTH_S = 1.5
const ACCENT_WINDOW_S = 0.07
const MIN_SECTION_S = 6
const LONG_UNSUNG_S = 30
const SOLO_ENERGY = 0.55
const PHRASE_BARS = 4
const SURROUND_S = 0.75
const SWELL_FROM = 0.55
const SWELL_FULL = 0.97
const PUNCHY_DB = 4
const PUNCHY_FLOOR = 0.35
const ONSET_RANK = 0.8
const ONSET_ATTACK_DB = 1.5
const ONSET_SNAP_S = 0.07
const ONSET_GRID = 4
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

const smoothstep = (from, to, x) => {
  const k = Math.min(1, Math.max(0, (x - from) / (to - from)))
  return k * k * (3 - 2 * k)
}

function ranks(values) {
  const sorted = Array.from(values).sort(numericAscending)
  const last = Math.max(1, sorted.length - 1)
  return Float32Array.from(values, value => lowerBound(sorted, value) / last)
}

function boxAverage(values, half) {
  const sums = new Float64Array(values.length + 1)
  for (let i = 0; i < values.length; i++) sums[i + 1] = sums[i] + values[i]
  return Float32Array.from(values, (_, i) => {
    const a = Math.max(0, i - half)
    const b = Math.min(values.length, i + half + 1)
    return (sums[b] - sums[a]) / (b - a)
  })
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
  return boxAverage(loud, Math.round(SMOOTH_S / STEP_S / 2))
}

function fineCurves(segments) {
  const n = segments.length
  if (n < 2) return { step: 0.1, swell: new Float32Array(1), level: new Float32Array(1), rise: new Float32Array(1), riseRank: new Float32Array(1), punchy: PUNCHY_FLOOR }
  const step = (segments[n - 1].start - segments[0].start) / (n - 1)
  const mean = Float32Array.from(segments, segment => segment.loudness)
  const surround = boxAverage(mean, Math.round(SURROUND_S / step))
  const rise = Float32Array.from(segments, (segment, i) => (segment.loudness_max ?? segment.loudness) - surround[i])
  const riseRank = ranks(rise)
  const sorted = Array.from(rise).sort(numericAscending)
  const spread = sorted[Math.floor(n * 0.95)] - sorted[Math.floor(n * 0.5)]
  const punchy = Math.min(1, Math.max(PUNCHY_FLOOR, spread / PUNCHY_DB))
  return {
    step,
    rise,
    riseRank,
    punchy,
    swell: Float32Array.from(riseRank, rank => smoothstep(SWELL_FROM, SWELL_FULL, rank) * punchy),
    level: ranks(boxAverage(mean, 1)),
  }
}

function findOnsets(fine, beats) {
  const { rise, riseRank, step } = fine
  const onsets = []
  const seen = new Set()
  for (let i = 2; i < rise.length - 1; i++) {
    if (riseRank[i] < ONSET_RANK || rise[i] < rise[i - 1] || rise[i] <= rise[i + 1]) continue
    if (rise[i] - Math.min(rise[i - 1], rise[i - 2]) < ONSET_ATTACK_DB) continue
    const time = (i + 0.5) * step
    const beat = lowerBound(beats, time) - 1
    if (beat < 0 || beat >= beats.length - 1) continue
    const span = beats[beat + 1] - beats[beat]
    const slot = Math.round(((time - beats[beat]) / span) * ONSET_GRID)
    if (slot <= 0 || slot >= ONSET_GRID) continue
    const snapped = beats[beat] + (slot / ONSET_GRID) * span
    const key = beat * ONSET_GRID + slot
    if (Math.abs(snapped - time) > ONSET_SNAP_S || seen.has(key)) continue
    seen.add(key)
    onsets.push({ time: snapped, beat, strength: (riseRank[i] - ONSET_RANK) / (1 - ONSET_RANK) })
  }
  return onsets
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

function splitUnsung(section, features, energyOf) {
  if (section.sung !== false || section.end - section.start < LONG_UNSUNG_S) return [section]
  const starts = [section.start, ...(features.sections || []).map(part => part?.start)
    .filter(start => Number.isFinite(start) && start > section.start + MIN_SECTION_S && start < section.end - MIN_SECTION_S)
    .sort(numericAscending)]
  return starts.map((start, i) => {
    const end = starts[i + 1] ?? section.end
    const energy = energyOf(start, end)
    const edge = (i === 0 && section.type === 'intro') || (i === starts.length - 1 && section.type === 'outro')
    const type = edge && energy < 0.5 ? section.type : energy >= 0.66 ? 'chorus' : energy >= 0.4 ? 'verse' : 'instrumental'
    return { ...section, start, end, type }
  })
}

function wordStart(word) {
  return typeof word === 'number' ? word : word?.start
}

function lyricLines(lines) {
  const parsed = (lines || [])
    .filter(line => Number.isFinite(line?.start) && Number.isFinite(line?.end) && line.end > line.start)
    .map(line => ({
      start: line.start,
      end: line.end,
      words: (line.words || []).map(wordStart).filter(Number.isFinite),
      key: String(line.line || '').toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, '').replace(/\s+/g, ' ').trim(),
    }))
    .sort((a, b) => a.start - b.start)
  const counts = new Map()
  for (const line of parsed) if (line.key) counts.set(line.key, (counts.get(line.key) || 0) + 1)
  const hooks = new Map()
  for (const line of parsed) {
    line.hook = -1
    if (!line.key || counts.get(line.key) < 2) continue
    if (!hooks.has(line.key)) hooks.set(line.key, hooks.size)
    line.hook = hooks.get(line.key)
  }
  return parsed
}

function sungShare(lines, from, to) {
  let sung = 0
  for (const line of lines) {
    if (line.start >= to) break
    sung += Math.max(0, Math.min(to, line.end) - Math.max(from, line.start))
  }
  return sung / Math.max(0.001, to - from)
}

function phrasesOf(section, beats, bpb, phase, measure) {
  const first = beats.findIndex(beat => beat >= section.start - 0.05)
  if (first < 0) return []
  let end = beats.findIndex(beat => beat >= section.end - 0.05)
  if (end < 0) end = beats.length
  if (end <= first) return []
  let downbeat = first
  while (downbeat < end && (((downbeat - phase) % bpb) + bpb) % bpb !== 0) downbeat++
  const starts = [first]
  for (let b = downbeat + PHRASE_BARS * bpb; b < end - bpb; b += PHRASE_BARS * bpb) starts.push(b)
  return starts.map((start, index) => {
    const stop = starts[index + 1] ?? end
    const from = beats[start]
    const to = stop < beats.length ? beats[stop] : section.end
    return { start, end: stop, index, count: starts.length, from, to, ...measure(from, to) }
  })
}

export function analyzeSong(features, lyricSections, lyrics) {
  const beats = features?.beats || []
  const segments = features?.loudness_segments || []
  const lastSegment = segments[segments.length - 1]
  const duration = features?.duration || (lastSegment ? lastSegment.start + lastSegment.duration : (beats[beats.length - 1] || 0) + 1)
  const bins = Math.max(1, Math.ceil(duration / STEP_S))
  const smooth = loudnessCurve(segments, bins)
  const energy = ranks(smooth)
  const fine = fineCurves(segments)
  const lines = lyricLines(lyrics)
  const words = Float64Array.from(lines.flatMap(line => line.words)).sort()
  const average = (values, step, start, end) => {
    const a = Math.max(0, Math.min(values.length - 1, Math.floor(start / step)))
    const b = Math.min(values.length, Math.max(a + 1, Math.ceil(end / step)))
    let total = 0
    for (let i = a; i < b; i++) total += values[i]
    return total / (b - a)
  }
  const energyOf = (start, end) => average(energy, STEP_S, start, end)
  const measure = (from, to) => {
    const quarter = (to - from) / 4
    return {
      energy: energyOf(from, to),
      punchy: average(fine.swell, fine.step, from, to),
      trend: energyOf(to - quarter, to) - energyOf(from, from + quarter),
      sung: sungShare(lines, from, to),
      wordRate: (lowerBound(words, to) - lowerBound(words, from)) / Math.max(0.001, to - from),
    }
  }
  const accents = beatAccents(beats, segments, smooth)
  const { bpb, phase } = meterOf(features || {}, accents.raw)
  const given = (lyricSections || []).filter(section => Number.isFinite(section?.start) && Number.isFinite(section?.end) && section.end > section.start)
  const base = (given.length >= 2 ? given : audioSections(features || {}, duration, energyOf)).flatMap(section => splitUnsung(section, features || {}, energyOf))
  let choruses = 0
  const sections = base.map(section => {
    const sectionEnergy = energyOf(section.start, section.end)
    let role = ROLES[section.type] || 'verse'
    if (role === 'break') role = sectionEnergy >= SOLO_ENERGY ? 'solo' : 'interlude'
    return { start: section.start, end: section.end, type: section.type, role, energy: sectionEnergy, sung: section.sung !== false, chorus: role === 'chorus' ? choruses++ : -1 }
  })
  let previous = null
  for (const section of sections) {
    section.final = section.role === 'chorus' && section.chorus === choruses - 1 && choruses > 1
    section.phrases = phrasesOf(section, beats, bpb, phase, measure)
    for (const phrase of section.phrases) {
      phrase.lift = previous ? phrase.energy - previous.energy : 0
      previous = phrase
    }
  }
  const period = beats.length > 1 ? (beats[beats.length - 1] - beats[0]) / (beats.length - 1) : 0.5
  return {
    beats, bpb, phase, period, duration, step: STEP_S, energy, accents: accents.ranked, sections, lines, words,
    fineStep: fine.step, swell: fine.swell, level: fine.level, punchy: fine.punchy, onsets: findOnsets(fine, beats),
  }
}

function energyAt(song, t) {
  return song.energy[Math.max(0, Math.min(song.energy.length - 1, Math.floor(t / song.step)))]
}

function fineAt(values, step, t) {
  const x = Math.max(0, Math.min(values.length - 1, t / step - 0.5))
  const i = Math.floor(x)
  const next = Math.min(values.length - 1, i + 1)
  return values[i] + (values[next] - values[i]) * (x - i)
}

export function signalsAt(song, t, out) {
  out.energy = energyAt(song, t)
  out.swell = fineAt(song.swell, song.fineStep, t)
  out.level = fineAt(song.level, song.fineStep, t)
  return out
}

export function barPosition(song, beat) {
  return (((beat - song.phase) % song.bpb) + song.bpb) % song.bpb
}

export function lineAt(song, t) {
  for (const line of song.lines) {
    if (line.start > t + 0.15) return null
    if (t < line.end) return line
  }
  return null
}
