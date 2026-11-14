// node state.mjs — what the phone's PLAiR tab is doing right now (changes nothing).
import { page } from './cdp.mjs'

const p = await page()
const out = await p.evaluate(`(async () => JSON.stringify({
  visible: document.visibilityState,
  size: [innerWidth, innerHeight, devicePixelRatio],
  quality: typeof window.__plairQuality === 'function' ? window.__plairQuality() : null,
  sceneThread: window.__plairScene?.thread?.(),
  art: window.__plairArt?.stats?.(),
  nav: [...document.querySelectorAll('[data-mobile-nav] button')].map(b => b.innerText.trim()),
  playing: [...document.querySelectorAll('audio')].some(a => !a.paused),
  elements: document.querySelectorAll('*').length,
  canvases: [...document.querySelectorAll('canvas')].map(c => c.width + 'x' + c.height),
  heapMB: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null,
  ua: navigator.userAgent,
}))()`)
console.log(out)
p.close()
process.exit(0)
