const FULL_TILT_DEGREES = 20
const REST_CUTOFF_HZ = 0.7
const CUTOFF_PER_DEGREE_PER_S = 0.5
const SPEED_CUTOFF_HZ = 1
const BLEND_MS = 8

const smoothing = (cutoffHz, seconds) => 1 / (1 + 1 / (2 * Math.PI * cutoffHz * seconds))
const clampUnit = value => Math.max(-1, Math.min(1, value))
const frameTime = () => document.timeline?.currentTime ?? performance.now()

let last = null
let valueX = 0
let valueY = 0
let speedX = 0
let speedY = 0
let fromX = 0
let fromY = 0
let fromAt = 0

const blended = (value, from, at) => value + (from - value) * Math.exp(-Math.max(0, at - fromAt) / BLEND_MS)

export const phoneTilt = {
  get lastAt() {
    return last ? last.at : -Infinity
  },

  sample(degreesX, degreesY, at) {
    if (!last) {
      valueX = fromX = degreesX
      valueY = fromY = degreesY
      fromAt = at
      last = { x: degreesX, y: degreesY, at }
      return
    }
    const seconds = (at - last.at) / 1000
    if (seconds <= 0) return
    fromX = blended(valueX, fromX, at)
    fromY = blended(valueY, fromY, at)
    fromAt = at
    const speedStep = smoothing(SPEED_CUTOFF_HZ, seconds)
    speedX += ((degreesX - last.x) / seconds - speedX) * speedStep
    speedY += ((degreesY - last.y) / seconds - speedY) * speedStep
    const step = smoothing(REST_CUTOFF_HZ + CUTOFF_PER_DEGREE_PER_S * Math.hypot(speedX, speedY), seconds)
    valueX += (degreesX - valueX) * step
    valueY += (degreesY - valueY) * step
    last = { x: degreesX, y: degreesY, at }
  },

  get parallaxX() {
    return last ? clampUnit(blended(valueX, fromX, frameTime()) / FULL_TILT_DEGREES) : 0
  },

  get parallaxY() {
    return last ? clampUnit(blended(valueY, fromY, frameTime()) / FULL_TILT_DEGREES) : 0
  },
}
