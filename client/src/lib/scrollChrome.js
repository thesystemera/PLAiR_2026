import { createFade, stepFade } from './motion'

const SCENE_HOLD_MS = 250

const fade = createFade(0)
const followers = new Set()
const afterWrites = []
let scrolling = false
let away = false
let extended = false
let inset = 0
let holdUntil = 0
let frame = null

function restInset() {
  const viewport = document.querySelector('[data-mobile-viewport]')
  return viewport ? Math.max(0, window.innerHeight - viewport.getBoundingClientRect().bottom) : 0
}

function notify() {
  for (const follow of followers) follow()
  while (afterWrites.length) afterWrites.shift()()
}

function tick(now) {
  frame = null
  const target = scrolling ? 1 : 0
  const reveal = stepFade(fade, target, now)
  if (!away && reveal === 0 && extended) {
    extended = false
    holdUntil = now + SCENE_HOLD_MS
  }
  notify()
  if (reveal !== target || (extended && !away)) frame = requestAnimationFrame(tick)
}

export const scrollChrome = {
  set(nextScrolling, nextAway) {
    if (nextScrolling === scrolling && nextAway === away) return
    if (nextAway && !extended) inset = restInset()
    scrolling = nextScrolling
    away = nextAway
    if (away) extended = true
    notify()
    if (frame === null) frame = requestAnimationFrame(tick)
  },
  extended: () => extended,
  afterWrites: (fn) => afterWrites.push(fn),
  reveal: () => stepFade(fade, fade.to, performance.now()),
  chromeReveal: () => (away || extended ? fade.value : 0),
  lift: () => (extended ? inset * (1 - fade.value) : 0),
  sceneLift: () => (extended || performance.now() < holdUntil ? inset * (1 - fade.value) : 0),
  follow(apply) {
    followers.add(apply)
    apply()
    return () => followers.delete(apply)
  },
}

function setStyle(node, prop, value) {
  if (node.style[prop] !== value) node.style[prop] = value
}

export const panelsInset = (node, px) => {
  setStyle(node, 'bottom', extended ? '0px' : `${px}px`)
}

export const tabBarPlace = (node, px) => {
  setStyle(node, 'position', extended ? 'absolute' : '')
  setStyle(node, 'left', extended ? '0px' : '')
  setStyle(node, 'right', extended ? '0px' : '')
  setStyle(node, 'bottom', extended ? `${px}px` : '')
  setStyle(node, 'height', '')
  setStyle(node, 'alignSelf', '')
}

export const railPlace = (node, px) => {
  setStyle(node, 'position', '')
  setStyle(node, 'left', '')
  setStyle(node, 'right', '')
  setStyle(node, 'bottom', '')
  setStyle(node, 'height', extended ? `calc(100% - ${px}px)` : '')
  setStyle(node, 'alignSelf', extended ? 'flex-start' : '')
}

export const liftWithPanel = (node) => {
  const lift = scrollChrome.lift()
  setStyle(node, 'transform', lift > 0 ? `translateY(${-lift}px)` : '')
}
