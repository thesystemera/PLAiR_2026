// node waveshot.mjs <out.png> — screenshot of the player's waveform on the phone (device pixels) with the song and
// how far it has played, for before/after comparisons (wavediff.py).
import { writeFileSync } from 'node:fs'
import { page } from './cdp.mjs'

const out = process.argv[2] || 'E:/scratch/plair/wave/shot.png'
const p = await page()
const info = JSON.parse(await p.evaluate(`(() => {
  const el = document.querySelector('[data-shader-panel="player"] .ui-track-in.cursor-pointer')
  if (!el) return JSON.stringify({ error: 'no waveform on screen' })
  const r = el.getBoundingClientRect()
  const window = el.querySelector('.overflow-hidden.will-change-transform')
  const played = window ? (window.getBoundingClientRect().right - r.left) / r.width : null
  return JSON.stringify({ x: r.left, y: r.top, width: r.width, height: r.height, played, song: navigator.mediaSession.metadata?.title || '', kind: el.querySelector('canvas') ? 'canvas' : 'elements', count: el.querySelectorAll('*').length })
})()`))
if (info.error) { console.log(info.error); process.exit(1) }
const shot = await p.send('Page.captureScreenshot', { format: 'png', clip: { x: info.x, y: info.y, width: info.width, height: info.height, scale: 1 } })
writeFileSync(out, Buffer.from(shot.result.data, 'base64'))
writeFileSync(out.replace(/\.png$/, '.json'), JSON.stringify(info))
console.log(JSON.stringify(info))
p.close()
process.exit(0)
