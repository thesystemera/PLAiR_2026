const EASE = {
  standard: 'easeInOut',
  out: 'easeOut',
  linear: 'linear',
  emphasized: [0.4, 0, 0.2, 1],
  decelerate: [0.2, 0.8, 0.2, 1],
  accelerate: [0.4, 0, 1, 1],
  pop: [0.34, 1.56, 0.64, 1],
}

export const DURATION = {
  instant: 0,
  tap: 0.09,
  micro: 0.14,
  exit: 0.16,
  quick: 0.2,
  badge: 0.22,
  enter: 0.26,
  art: 0.28,
  base: 0.3,
  pop: 0.34,
  burst: 0.46,
  fade: 0.5,
  slow: 0.6,
  theme: 0.7,
  breathe: 2.4,
}

export const SPRING = {
  panel: { type: 'spring', stiffness: 300, damping: 30 },
  toast: { type: 'spring', stiffness: 300, damping: 25 },
  dialog: { type: 'spring', stiffness: 380, damping: 32, mass: 0.9 },
  snappy: { type: 'spring', stiffness: 520, damping: 34, mass: 0.8 },
  message: { type: 'spring', stiffness: 420, damping: 30, mass: 0.7 },
  press: { type: 'spring', stiffness: 800, damping: 34, mass: 0.6 },
  pop: { type: 'spring', stiffness: 560, damping: 15, mass: 0.7 },
  badge: { type: 'spring', stiffness: 620, damping: 24, mass: 0.7 },
  hover: { type: 'spring', stiffness: 500, damping: 25 },
}

export const TWEEN = {
  enter: { duration: DURATION.enter, ease: EASE.decelerate },
  exit: { duration: DURATION.exit, ease: EASE.accelerate },
  fade: { duration: DURATION.quick, ease: EASE.out },
  collapse: { duration: 0.22, ease: EASE.emphasized },
  layout: { duration: DURATION.base, ease: EASE.emphasized },
  micro: { duration: DURATION.micro, ease: EASE.decelerate },
  track: { duration: DURATION.theme, ease: EASE.emphasized },
}

function cubicBezier(x1, y1, x2, y2) {
  const axis = (a, b, t) => ((1 - 3 * b + 3 * a) * t + (3 * b - 6 * a)) * t * t + 3 * a * t
  const slope = (a, b, t) => 3 * (1 - 3 * b + 3 * a) * t * t + 2 * (3 * b - 6 * a) * t + 3 * a
  return (x) => {
    if (x <= 0) return 0
    if (x >= 1) return 1
    let t = x
    for (let i = 0; i < 8; i++) {
      const error = axis(x1, x2, t) - x
      if (Math.abs(error) < 1e-5) break
      const d = slope(x1, x2, t)
      if (Math.abs(d) < 1e-6) break
      t -= error / d
    }
    return axis(y1, y2, Math.min(1, Math.max(0, t)))
  }
}

export const trackFadeEase = cubicBezier(...EASE.emphasized)

const STAGGER = {
  step: 0.035,
  max: 0.3,
  delay: 0.04,
}

export const staggerDelay = (index, step = STAGGER.step, max = STAGGER.max) =>
  Math.min(Math.max(index, 0) * step, max)

export const MOTION = {
  spring: SPRING.panel,
  toastSpring: SPRING.toast,
  fade: { duration: DURATION.fade, ease: EASE.standard },
  quick: { duration: DURATION.quick },
  quickOpacity: { opacity: { duration: DURATION.quick } },
  quickScale: { scale: { duration: DURATION.quick } },
  quickOpacityScale: { opacity: { duration: DURATION.quick }, scale: { duration: DURATION.quick } },
  dialog: { duration: DURATION.base, ease: EASE.emphasized },
  progress: { duration: DURATION.base, ease: EASE.out },
  settle: { duration: DURATION.slow, ease: EASE.out },
  spin: { duration: 1, repeat: Infinity, ease: EASE.linear },
  pulse: { duration: 0.8, repeat: Infinity, ease: EASE.standard },
  breathe: { duration: 1.5, repeat: Infinity, ease: EASE.standard },
  glow: { duration: 3, repeat: Infinity, ease: EASE.standard },
  shimmer: { duration: 2, repeat: Infinity },
  beat: { duration: 1, repeat: Infinity },
  base: { duration: DURATION.base },
}

export const VARIANTS = {
  expand: {
    collapsed: {
      height: 0,
      opacity: 0,
      overflow: 'hidden',
      transition: { height: TWEEN.collapse, opacity: { duration: DURATION.exit } },
    },
    open: {
      height: 'auto',
      opacity: 1,
      transition: { height: SPRING.panel, opacity: { duration: DURATION.quick } },
      transitionEnd: { overflow: 'visible' },
    },
  },
  expandReduced: {
    collapsed: { height: 0, opacity: 0, overflow: 'hidden', transition: { duration: 0 } },
    open: { height: 'auto', opacity: 1, transition: { duration: 0 }, transitionEnd: { overflow: 'visible' } },
  },
  expandContent: {
    collapsed: { opacity: 0, y: -8, transition: TWEEN.exit },
    open: { opacity: 1, y: 0, transition: { ...TWEEN.enter, delay: 0.05 } },
  },
  stagger: {
    hidden: {},
    show: { transition: { staggerChildren: STAGGER.step, delayChildren: STAGGER.delay } },
  },
  staggerItem: {
    hidden: { opacity: 0, y: 10 },
    show: { opacity: 1, y: 0, transition: TWEEN.enter },
  },
  staggerSection: {
    hidden: { opacity: 0, y: 10 },
    show: { opacity: 1, y: 0, transition: { ...TWEEN.enter, staggerChildren: 0.03, delayChildren: 0.02 } },
  },
}

export const PRESETS = {
  fade: {
    initial: { opacity: 0 },
    animate: { opacity: 1, transition: TWEEN.fade },
    exit: { opacity: 0, transition: TWEEN.exit },
  },
  fadeSlow: {
    initial: { opacity: 0 },
    animate: { opacity: 1, transition: MOTION.fade },
    exit: { opacity: 0, transition: MOTION.fade },
  },
  trackSwap: {
    initial: { opacity: 0 },
    animate: { opacity: 1, transition: TWEEN.track },
    exit: { opacity: 0, transition: TWEEN.track },
  },
  fadeSlide: {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0, transition: TWEEN.enter },
    exit: { opacity: 0, y: -6, transition: TWEEN.exit },
  },
  panelSwap: {
    initial: { opacity: 0, y: 6 },
    animate: { opacity: 1, y: 0, transition: TWEEN.enter },
    exit: { opacity: 0, transition: TWEEN.exit },
  },
  stepSwap: {
    initial: { opacity: 0, y: 10 },
    animate: { opacity: 1, y: 0, transition: TWEEN.enter },
    exit: { opacity: 0, y: -8, transition: TWEEN.exit },
  },
  modalBackdrop: {
    initial: { opacity: 0 },
    animate: { opacity: 1, transition: { duration: DURATION.quick, ease: EASE.out } },
    exit: { opacity: 0, transition: { duration: DURATION.quick, ease: EASE.accelerate, delay: 0.04 } },
  },
  modalDialog: {
    initial: { opacity: 0, scale: 0.94, y: 18 },
    animate: { opacity: 1, scale: 1, y: 0, transition: { ...SPRING.dialog, opacity: { duration: DURATION.quick } } },
    exit: { opacity: 0, scale: 0.96, y: 10, transition: TWEEN.exit },
  },
  dropdown: {
    initial: { opacity: 0, y: -4, scale: 0.98 },
    animate: { opacity: 1, y: 0, scale: 1, transition: SPRING.snappy },
    exit: { opacity: 0, y: -4, scale: 0.98, transition: TWEEN.exit },
  },
  tooltip: {
    initial: { opacity: 0, y: 4, scale: 0.97 },
    animate: { opacity: 1, y: 0, scale: 1, transition: TWEEN.enter },
    exit: { opacity: 0, y: 4, scale: 0.97, transition: TWEEN.exit },
  },
  listItem: {
    initial: { opacity: 0, y: 10 },
    animate: { opacity: 1, y: 0, transition: TWEEN.enter },
    exit: { opacity: 0, transition: TWEEN.exit },
  },
  listReorder: {
    initial: { opacity: 0, x: -16 },
    animate: { opacity: 1, x: 0 },
    transition: { ...TWEEN.enter, layout: TWEEN.layout },
    exit: {
      opacity: 0,
      x: 24,
      transition: { opacity: { duration: 0.14, ease: EASE.out }, x: TWEEN.exit },
    },
    bulkExit: {
      opacity: 0,
      transition: { duration: DURATION.exit, ease: EASE.out },
    },
  },
  iconSwap: {
    initial: { opacity: 0, scale: 0.6, rotate: -30 },
    animate: { opacity: 1, scale: 1, rotate: 0, transition: SPRING.snappy },
    exit: { opacity: 0, scale: 0.6, rotate: 30, transition: { duration: 0.12 } },
  },
  emptyState: {
    initial: { opacity: 0, y: 12, scale: 0.98 },
    animate: { opacity: 1, y: 0, scale: 1, transition: { ...TWEEN.enter, delay: 0.05 } },
    exit: { opacity: 0, transition: TWEEN.exit },
  },
  press: {
    whileTap: { scale: 0.9, transition: SPRING.press },
    transition: SPRING.pop,
  },
  hoverPress: {
    whileHover: { scale: 1.05, transition: SPRING.snappy },
    whileTap: { scale: 0.9, transition: SPRING.press },
    transition: SPRING.pop,
  },
  hoverPressLarge: {
    whileHover: { scale: 1.1, transition: SPRING.snappy },
    whileTap: { scale: 0.9, transition: SPRING.press },
    transition: SPRING.pop,
  },
  softPress: {
    whileHover: { scale: 1.02, transition: SPRING.snappy },
    whileTap: { scale: 0.97, transition: SPRING.press },
    transition: SPRING.pop,
  },
  pop: {
    initial: { opacity: 0, scale: 0.6 },
    animate: { opacity: 1, scale: 1, transition: { ...SPRING.pop, opacity: TWEEN.micro } },
    exit: { opacity: 0, scale: 0.6, transition: TWEEN.exit },
  },
  badgeSwap: {
    initial: { opacity: 0, scale: 0.7, y: -6 },
    animate: {
      opacity: 1,
      scale: 1,
      y: 0,
      transition: { ...SPRING.badge, delay: DURATION.micro, opacity: { ...TWEEN.micro, delay: DURATION.micro } },
    },
    exit: { opacity: 0, scale: 0.8, y: 6, transition: TWEEN.exit },
  },
  artPop: {
    initial: { opacity: 0, scale: 0.94 },
    animate: {
      opacity: 1,
      scale: [0.94, 1.02, 1],
      transition: { duration: DURATION.art, ease: EASE.decelerate, times: [0, 0.6, 1], opacity: TWEEN.micro },
    },
    exit: { opacity: 0, transition: TWEEN.exit },
  },
  nope: {
    animate: { x: [0, -4, 4, -2, 2, 0], rotate: [0, -8, 6, -3, 2, 0], transition: { duration: DURATION.pop, ease: EASE.out } },
  },
}

export const messageMotion = (fromRight) => ({
  initial: { opacity: 0, y: 14, x: fromRight ? 16 : -16, scale: 0.94 },
  animate: { opacity: 1, y: 0, x: 0, scale: 1, transition: { ...SPRING.message, opacity: { duration: DURATION.quick } } },
  exit: { opacity: 0, scale: 0.96, transition: TWEEN.exit },
  style: { transformOrigin: fromRight ? '100% 100%' : '0% 100%' },
})

export const CSS_EASE = {
  standard: 'ease-in-out',
  out: 'ease-out',
  linear: 'linear',
  decelerate: 'cubic-bezier(0.2, 0.8, 0.2, 1)',
  accelerate: 'cubic-bezier(0.4, 0, 1, 1)',
  emphasized: 'cubic-bezier(0.4, 0, 0.2, 1)',
  springy: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
}

const ms = (seconds) => `${Math.round(seconds * 1000)}ms`

const PRESS_SCALE = `scale ${ms(DURATION.micro)} ${CSS_EASE.decelerate}`
const THEME_ALL = `all ${ms(DURATION.theme)} ${CSS_EASE.standard}`

export const CSS_TRANSITION = {
  theme: `${THEME_ALL}, transform ${ms(DURATION.micro)} ${CSS_EASE.standard}, ${PRESS_SCALE}`,
  themeAll: `${THEME_ALL}, ${PRESS_SCALE}`,
  themeOpacity: `${THEME_ALL}, opacity ${ms(DURATION.micro)} ${CSS_EASE.standard}, ${PRESS_SCALE}`,
  themeBackground: `background ${ms(DURATION.theme)} ${CSS_EASE.standard}, ${PRESS_SCALE}`,
  themeBackgroundColor: `background-color ${ms(DURATION.theme)} ${CSS_EASE.standard}, ${PRESS_SCALE}`,
  themeBorder: `border-color ${ms(DURATION.theme)} ${CSS_EASE.standard}`,
  themeBackgroundTransform: `background ${ms(DURATION.theme)} ${CSS_EASE.standard}, transform 100ms ${CSS_EASE.linear}`,
  themeBackgroundScale: `background-color ${ms(DURATION.theme)} ${CSS_EASE.standard}, transform ${ms(DURATION.base)} ${CSS_EASE.out}`,
  quick: `all ${ms(DURATION.micro)} ${CSS_EASE.standard}, ${PRESS_SCALE}`,
  fadeOpacity: `opacity ${ms(DURATION.base)} ${CSS_EASE.standard}`,
  fade: `opacity ${ms(DURATION.base)} ${CSS_EASE.out}`,
  mask: `mask-image ${ms(DURATION.fade)} ${CSS_EASE.out}, -webkit-mask-image ${ms(DURATION.fade)} ${CSS_EASE.out}`,
  progress: `transform 100ms ${CSS_EASE.linear}`,
  meter: `transform 75ms ${CSS_EASE.out}, opacity ${ms(DURATION.quick)} ${CSS_EASE.out}`,
  bar: `transform ${ms(DURATION.base)} ${CSS_EASE.out}`,
  buffered: `transform ${ms(DURATION.base)} ${CSS_EASE.out}`,
  springyPress: `transform ${ms(DURATION.micro)} ${CSS_EASE.springy}`,
  chevron: `transform 280ms ${CSS_EASE.decelerate}`,
  fill: `opacity ${ms(DURATION.micro)} ${CSS_EASE.out}, transform ${ms(DURATION.pop)} ${CSS_EASE.springy}`,
  press: PRESS_SCALE,
}

export const MICRO = {
  richTier: 1,
  artScrollTier: 0,
  artMemory: 4000,
  tapSlop: 10,
  release: {
    duration: DURATION.pop,
    tap: { peak: 1.14, dip: 0.97 },
    press: { peak: 1.05, dip: 0.99 },
    wide: { peak: 1.025, dip: 0.995 },
    wideAt: 120,
  },
  pop: {
    duration: DURATION.pop + 0.04,
    frames: [1, 1.35, 0.92, 1],
    offsets: [0, 0.35, 0.7, 1],
  },
  nope: {
    duration: DURATION.pop,
    frames: [[0, 0], [-3, -10], [3, 8], [-2, -4], [1, 2], [0, 0]],
  },
  nudge: {
    duration: DURATION.enter,
    distance: 5,
  },
  burst: {
    duration: DURATION.burst,
    dots: 6,
    ringFrom: 0.4,
    ringTo: 1.7,
    spread: 0.95,
    dotSize: 5,
  },
  artPop: {
    duration: DURATION.art,
    frames: [0.94, 1.02, 1],
  },
  artPopLarge: {
    duration: DURATION.art + 0.06,
    frames: [0.97, 1.01, 1],
  },
  artBump: {
    duration: DURATION.art,
    frames: [1, 1.03, 1],
  },
  artReveal: {
    duration: DURATION.art,
    fromScale: 1.06,
  },
  arrival: {
    delay: DURATION.base * 0.8,
    duration: DURATION.slow,
    glow: [0, 0.9, 0],
    strongGlow: [0, 1, 0],
    art: [1, 1.1, 1],
  },
}
