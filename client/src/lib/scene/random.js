export function seededRandom(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6D2B79F5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function pickWeighted(rand, weighted) {
  let total = 0
  for (const [, weight] of weighted) total += weight
  let roll = rand() * total
  for (const [value, weight] of weighted) {
    roll -= weight
    if (roll < 0) return value
  }
  return weighted[weighted.length - 1][0]
}
