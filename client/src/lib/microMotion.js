import { CSS_EASE, MICRO } from './motion'

const TOP_TIER = 4
const noop = () => {}

const policy = {
  tier: TOP_TIER,
  reduceMotion: typeof window !== 'undefined' && typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches,
}

function motionLevel() {
  if (policy.reduceMotion) return 'off'
  return policy.tier >= MICRO.richTier ? 'full' : 'lite'
}

export function setMotionPolicy({ tier, reduceMotion }) {
  if (typeof tier === 'number') policy.tier = tier
  if (typeof reduceMotion === 'boolean') policy.reduceMotion = reduceMotion
  if (typeof document !== 'undefined') document.documentElement.dataset.motion = motionLevel()
}

export const canAnimate = () => !policy.reduceMotion
export const canDecorate = () => !policy.reduceMotion && policy.tier >= MICRO.richTier

const secondsToMs = (seconds) => Math.round(seconds * 1000)

function play(el, keyframes, options) {
  if (!el || typeof el.animate !== 'function' || policy.reduceMotion) return null
  try {
    return el.animate(keyframes, options)
  } catch {
    return null
  }
}

const running = new WeakMap()

function replace(el, keyframes, options) {
  const previous = running.get(el)
  if (previous) previous.cancel()
  const animation = play(el, keyframes, options)
  if (animation) running.set(el, animation)
  return animation
}

function scaleFrames(values, offsets, base = '') {
  const prefix = base ? `${base} ` : ''
  return values.map((value, index) => {
    const frame = { transform: `${prefix}scale(${value})` }
    if (offsets) frame.offset = offsets[index]
    return frame
  })
}

function releaseFrames(base, { peak, dip }) {
  const prefix = base ? `${base} ` : ''
  return [
    { transform: `${prefix}scale(1)`, easing: CSS_EASE.decelerate },
    { transform: `${prefix}scale(${peak})`, offset: 0.34, easing: CSS_EASE.standard },
    { transform: `${prefix}scale(${dip})`, offset: 0.68, easing: CSS_EASE.decelerate },
    { transform: `${prefix}scale(1)` },
  ]
}

const OWN_TRANSFORM = /(^|\s)(-?translate-|rotate-|-?scale-)/

function releaseSpec(el) {
  const { release } = MICRO
  if (el.classList.contains('ui-tap')) return release.tap
  return el.offsetWidth > release.wideAt ? release.wide : release.press
}

function baseTransform(el) {
  if (!el.style.transform && !OWN_TRANSFORM.test(el.className)) return ''
  const computed = getComputedStyle(el).transform
  return computed && computed !== 'none' ? computed : ''
}

export function releasePop(el, spec = releaseSpec(el), base = baseTransform(el)) {
  if (!el || !canAnimate()) return
  replace(el, releaseFrames(base, spec), { duration: secondsToMs(MICRO.release.duration) })
}

export function pop(el) {
  const { pop: spec } = MICRO
  replace(el, scaleFrames(spec.frames, spec.offsets), { duration: secondsToMs(spec.duration), easing: CSS_EASE.out })
}

export function nope(el) {
  const { nope: spec } = MICRO
  const frames = spec.frames.map(([x, deg]) => ({ transform: `translateX(${x}px) rotate(${deg}deg)` }))
  replace(el, frames, { duration: secondsToMs(spec.duration), easing: CSS_EASE.out })
}

export function nudge(el, direction) {
  const { nudge: spec } = MICRO
  const distance = direction === 'left' ? -spec.distance : spec.distance
  replace(el, [
    { transform: 'translateX(0px)', easing: CSS_EASE.decelerate },
    { transform: `translateX(${distance}px)`, offset: 0.35, easing: CSS_EASE.springy },
    { transform: 'translateX(0px)' },
  ], { duration: secondsToMs(spec.duration) })
}

const isOffscreen = (el) => !el || el.dataset.offscreen === 'true'

export function artPop(el, variant = 'artPop') {
  if (isOffscreen(el)) return
  const spec = MICRO[variant] || MICRO.artPop
  replace(el, scaleFrames(spec.frames, [0, 0.6, 1]), { duration: secondsToMs(spec.duration), easing: CSS_EASE.decelerate })
}

export function artReveal(img) {
  const { artReveal: spec } = MICRO
  replace(img, [
    { opacity: 0, transform: `scale(${spec.fromScale})` },
    { opacity: 1, transform: 'scale(1)' },
  ], { duration: secondsToMs(spec.duration), easing: CSS_EASE.decelerate })
}

export function arrivalGlow(el, strong = false) {
  const { arrival } = MICRO
  const values = strong ? arrival.strongGlow : arrival.glow
  play(el, values.map(opacity => ({ opacity })), {
    duration: secondsToMs(arrival.duration),
    delay: strong ? 0 : secondsToMs(arrival.delay),
    easing: CSS_EASE.standard,
  })
}

export function arrivalPulse(el) {
  const { arrival } = MICRO
  replace(el, scaleFrames(arrival.art, [0, 0.4, 1]), {
    duration: secondsToMs(arrival.duration),
    delay: secondsToMs(arrival.delay),
    easing: CSS_EASE.decelerate,
  })
}

export function burst(anchor, color) {
  if (!anchor || !anchor.isConnected || !canDecorate() || typeof document === 'undefined') return
  const rect = anchor.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const { burst: spec } = MICRO
  const size = Math.max(rect.width, rect.height)
  const radius = size * spec.spread
  const layer = document.createElement('div')
  layer.className = 'ui-burst'
  layer.setAttribute('aria-hidden', 'true')
  layer.style.left = `${rect.left + rect.width / 2}px`
  layer.style.top = `${rect.top + rect.height / 2}px`
  layer.style.color = color

  const ring = document.createElement('span')
  ring.className = 'ui-burst-ring'
  ring.style.width = `${size}px`
  ring.style.height = `${size}px`
  layer.appendChild(ring)

  const dots = []
  for (let i = 0; i < spec.dots; i++) {
    const dot = document.createElement('span')
    dot.className = 'ui-burst-dot'
    dot.style.width = `${spec.dotSize}px`
    dot.style.height = `${spec.dotSize}px`
    layer.appendChild(dot)
    dots.push(dot)
  }
  document.body.appendChild(layer)

  const duration = secondsToMs(spec.duration)
  const center = 'translate(-50%, -50%)'
  const animations = [play(ring, [
    { transform: `${center} scale(${spec.ringFrom})`, opacity: 0.9 },
    { transform: `${center} scale(${spec.ringTo})`, opacity: 0 },
  ], { duration, easing: CSS_EASE.decelerate })]

  const start = Math.PI / spec.dots
  dots.forEach((dot, i) => {
    const angle = start + (i / spec.dots) * Math.PI * 2
    const x = Math.cos(angle) * radius
    const y = Math.sin(angle) * radius
    animations.push(play(dot, [
      { transform: `${center} translate(0px, 0px) scale(1)`, opacity: 1 },
      { transform: `${center} translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(0.2)`, opacity: 0 },
    ], { duration, easing: CSS_EASE.decelerate }))
  })

  let removed = false
  const remove = () => {
    if (removed) return
    removed = true
    layer.remove()
  }
  const first = animations.find(Boolean)
  if (first) {
    first.onfinish = remove
    first.oncancel = remove
  }
  setTimeout(remove, duration + 120)
}

let pressState = null
let pressInstalled = false

function onPressDown(event) {
  if (event.button > 0 || !(event.target instanceof Element)) {
    pressState = null
    return
  }
  const el = event.target.closest('.ui-tap, .ui-press')
  if (!el || el.disabled || el.getAttribute('aria-disabled') === 'true' || !canAnimate()) {
    pressState = null
    return
  }
  const previous = running.get(el)
  if (previous) {
    previous.cancel()
    running.delete(el)
  }
  pressState = { el, x: event.clientX, y: event.clientY, id: event.pointerId, spec: releaseSpec(el), base: baseTransform(el) }
}

function onPressUp(event) {
  const state = pressState
  pressState = null
  if (!state || state.id !== event.pointerId || !state.el.isConnected || state.el.disabled) return
  if (Math.abs(event.clientX - state.x) > MICRO.tapSlop || Math.abs(event.clientY - state.y) > MICRO.tapSlop) return
  releasePop(state.el, state.spec, state.base)
}

function onPressCancel() {
  pressState = null
}

export function installPressFeedback() {
  if (pressInstalled || typeof document === 'undefined') return
  pressInstalled = true
  const options = { capture: true, passive: true }
  document.addEventListener('pointerdown', onPressDown, options)
  document.addEventListener('pointerup', onPressUp, options)
  document.addEventListener('pointercancel', onPressCancel, options)
  document.documentElement.dataset.motion = motionLevel()
}

const readyImages = new WeakSet()

const isImageReady = (img) => !!img && img.complete && img.naturalWidth > 0

export function noteImageMount(img) {
  if (isImageReady(img)) readyImages.add(img)
}

export function revealOnLoad(event) {
  const img = event.currentTarget
  if (!img || readyImages.has(img)) return
  readyImages.add(img)
  artReveal(img)
}

const artSeen = new Set()
const artStates = new WeakMap()
let artObserver = null

function rememberArt(id) {
  if (artSeen.size >= MICRO.artMemory) artSeen.clear()
  artSeen.add(id)
}

function finishArt(box, state, reveal) {
  artStates.delete(box)
  rememberArt(state.id)
  if (reveal) {
    if (state.img) artReveal(state.img)
    artPop(box, 'artBump')
  } else if (policy.tier >= MICRO.artScrollTier) {
    artPop(box)
  }
}

function onArtIntersect(entries) {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue
    const state = artStates.get(entry.target)
    if (!state) continue
    artObserver.unobserve(entry.target)
    state.visible = true
    if (state.loaded) finishArt(entry.target, state, false)
  }
}

export function watchArt(box, id, skip = false) {
  if (!box || !id || artSeen.has(id)) return noop
  if (!artObserver && typeof IntersectionObserver !== 'undefined') artObserver = new IntersectionObserver(onArtIntersect)
  if (skip || !artObserver || !canAnimate()) {
    rememberArt(id)
    return noop
  }
  const img = box.querySelector('img')
  const loaded = isImageReady(img)
  if (loaded) readyImages.add(img)
  artStates.set(box, { id, img, loaded, visible: false })
  artObserver.observe(box)
  return () => {
    artObserver.unobserve(box)
    artStates.delete(box)
  }
}

export function artLoaded(box, img) {
  const state = box ? artStates.get(box) : null
  if (!state || state.loaded) return
  state.loaded = true
  state.img = img
  readyImages.add(img)
  if (state.visible) finishArt(box, state, true)
}

let offscreenObserver = null

export function watchOffscreen(el) {
  if (!el || typeof IntersectionObserver === 'undefined') return noop
  if (!offscreenObserver) {
    offscreenObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) entry.target.dataset.offscreen = entry.isIntersecting ? 'false' : 'true'
    })
  }
  offscreenObserver.observe(el)
  return () => offscreenObserver.unobserve(el)
}
