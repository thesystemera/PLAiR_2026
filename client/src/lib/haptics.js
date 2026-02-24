const HAPTIC_PATTERNS = {
  light: 50,
  medium: 100,
  strong: 150,
  double: [75, 50, 75],
  success: [50, 40, 100],
  error: [100, 50, 100, 50, 100],
  record: [150, 100, 150]
}

export const triggerHaptic = (type = 'medium') => {
  if ('vibrate' in navigator) {
    const pattern = HAPTIC_PATTERNS[type] || HAPTIC_PATTERNS.medium
    try {
      navigator.vibrate(pattern)
    } catch {
      // Haptic feedback errors are intentionally suppressed
    }
  }
}