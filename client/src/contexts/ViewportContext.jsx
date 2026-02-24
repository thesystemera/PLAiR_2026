import { createContext, useContext, useState, useMemo, useLayoutEffect } from 'react'

const ViewportContext = createContext(null)

const BREAKPOINTS = { xs: 0, sm: 640, md: 768, lg: 1024, xl: 1440, '2xl': 1920, '3xl': 2560 }

const getBp = (w) => {
  if (w >= BREAKPOINTS['3xl']) return '3xl'
  if (w >= BREAKPOINTS['2xl']) return '2xl'
  if (w >= BREAKPOINTS.xl) return 'xl'
  if (w >= BREAKPOINTS.lg) return 'lg'
  if (w >= BREAKPOINTS.md) return 'md'
  if (w >= BREAKPOINTS.sm) return 'sm'
  return 'xs'
}

const detectDevice = () => {
  if (typeof window === 'undefined') return {
    isIOS: false,
    isAndroid: false,
    isMobileDevice: false,
    isSafari: false,
    isCompatible: true
  }

  const ua = window.navigator.userAgent || ''

  const isIOS = /iPad|iPhone|iPod/.test(ua) ||
                (ua.includes('Macintosh') && navigator.maxTouchPoints > 1)

  const isAndroid = /Android/.test(ua)
  const isNaturallyLandscape = window.screen.width > window.screen.height
  const isMobileDevice = (isIOS || isAndroid) && !isNaturallyLandscape

  const isSafari = /^((?!chrome|android).)*safari/i.test(ua) ||
                   (isIOS && (/CriOS|FxiOS/.test(ua) || !(/Chrome/.test(ua))))

  const isCompatible = !isIOS

  return { isIOS, isAndroid, isMobileDevice, isSafari, isCompatible }
}

export function ViewportProvider({ children }) {
  const deviceInfo = useMemo(() => detectDevice(), [])

  const [viewport, setViewport] = useState(() => ({
    width: typeof window !== 'undefined' ? window.innerWidth : 1920,
    height: typeof window !== 'undefined' ? window.innerHeight : 1080,
    dpr: typeof window !== 'undefined' ? (window.devicePixelRatio || 1) : 1,
  }))

  useLayoutEffect(() => {
    let ticking = false
    let lastBp = getBp(window.innerWidth)

    const handleResize = () => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          const width = window.innerWidth
          const newBp = getBp(width)
          if (newBp !== lastBp) {
            setViewport({ width, height: window.innerHeight, dpr: window.devicePixelRatio || 1 })
            lastBp = newBp
          }
          ticking = false
        })
        ticking = true
      }
    }

    window.addEventListener('resize', handleResize)
    const orientationMatch = window.matchMedia('(orientation: portrait)')
    orientationMatch.addEventListener('change', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      orientationMatch.removeEventListener('change', handleResize)
    }
  }, [])

  useLayoutEffect(() => {
    const w = viewport.width
    const scale = w < BREAKPOINTS.lg ? 1 : w >= 3840 ? 1.0 : w >= BREAKPOINTS['3xl'] ? 0.85 : w >= BREAKPOINTS['2xl'] ? 0.7 : w >= BREAKPOINTS.xl ? 0.75 : 0.8
    document.documentElement.style.fontSize = `${scale * 100}%`
  }, [viewport.width])

  const value = useMemo(() => {
    const { width, height, dpr } = viewport
    const bp = getBp(width)

    const scale = width >= 3840 ? 1.8 : width >= BREAKPOINTS['3xl'] ? 1.4 : width >= BREAKPOINTS['2xl'] ? 1.0 : width >= BREAKPOINTS.xl ? 0.7 : width >= BREAKPOINTS.lg ? 0.55 : width >= BREAKPOINTS.md ? 0.45 : 0.4

    return {
      width, height, dpr, scale,
      breakpoint: bp,
      breakpoints: BREAKPOINTS,

      isMobile: deviceInfo.isMobileDevice,
      isNarrowViewport: width < BREAKPOINTS.md,

      isTablet: bp === 'md',
      isDesktop: width >= BREAKPOINTS.lg,
      isWidescreen: width >= BREAKPOINTS.xl,
      is4K: bp === '3xl',
      isPortrait: height > width,
      isLandscape: width >= height,
      isRetina: dpr >= 2,

      isXS: bp === 'xs', isSM: bp === 'sm', isMD: bp === 'md',
      isLG: bp === 'lg', isXL: bp === 'xl', is2XL: bp === '2xl', is3XL: bp === '3xl',

      minSM: width >= BREAKPOINTS.sm, minMD: width >= BREAKPOINTS.md, minLG: width >= BREAKPOINTS.lg,
      minXL: width >= BREAKPOINTS.xl, min2XL: width >= BREAKPOINTS['2xl'], min3XL: width >= BREAKPOINTS['3xl'],

      maxSM: width < BREAKPOINTS.md, maxMD: width < BREAKPOINTS.lg, maxLG: width < BREAKPOINTS.xl,
      maxXL: width < BREAKPOINTS['2xl'], max2XL: width < BREAKPOINTS['3xl'],

      isIOS: deviceInfo.isIOS,
      isAndroid: deviceInfo.isAndroid,
      isSafari: deviceInfo.isSafari,
      isCompatible: deviceInfo.isCompatible,
    }
  }, [viewport, deviceInfo])

  return <ViewportContext.Provider value={value}>{children}</ViewportContext.Provider>
}

export function useViewport() {
  const context = useContext(ViewportContext)
  if (!context) throw new Error('useViewport must be used within ViewportProvider')
  return context
}