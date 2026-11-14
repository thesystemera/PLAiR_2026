// node fillcheck.mjs <out dir> — pixel check on the phone of the fills that replaced CSS masks: the header backdrop
// (feathered on four sides, `featheredFill` in themeManager.js), the tab bar and the landscape rail. Each old (masked)
// and new (mask-free) version is drawn over the same busy test pattern in a full-screen overlay and screenshotted;
// fillcheck_diff.py prints the differences. Add `dark` for a pattern as dark as the app's glass (the default is far brighter, to exaggerate). The overlay covers the app for a few seconds.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { page, sleep } from './cdp.mjs'

const outDir = process.argv[2] || 'E:/scratch/plair/fillcheck'
mkdirSync(outDir, { recursive: true })
const theme = readFileSync(fileURLToPath(new URL('../../../client/src/lib/themeManager.js', import.meta.url)), 'utf8')
const source = theme.slice(theme.indexOf('const FEATHER_FAR_PX'), theme.indexOf('\n}\n', theme.indexOf('export function featheredFill')) + 3).replace('export function', 'function')
const featheredFill = new Function(`${source}; return featheredFill`)()

const edgeMask = (top, bottom, side) => `linear-gradient(0deg, transparent, black ${bottom}, black calc(100% - ${top}), transparent), linear-gradient(90deg, transparent, black ${side}, black calc(100% - ${side}), transparent)`
const verticalMask = fade => `linear-gradient(to bottom, transparent 0%, black ${fade}, black calc(100% - ${fade}), transparent 100%)`
const verticalFill = (color, fade) => `linear-gradient(to bottom, transparent 0%, ${color} ${fade}, ${color} calc(100% - ${fade}), transparent 100%)`
const masked = mask => `-webkit-mask-image:${mask};mask-image:${mask};`

const CASES = {
  'header 0.9': {
    box: 'left:1px;top:20px;width:373px;height:72px;',
    old: `border-radius:12px;background:rgba(0,0,0,0.9);${masked(edgeMask('8px', '8px', '8px'))}-webkit-mask-composite:source-in;mask-composite:intersect;`,
    now: `border-radius:12px;background-image:${featheredFill(0.9, 8)};background-size:100% 100%;background-repeat:no-repeat;`,
  },
  'header 0.55, odd size': {
    box: 'left:7.3px;top:20.4px;width:301.7px;height:65.2px;',
    old: `border-radius:12px;background:rgba(0,0,0,0.55);${masked(edgeMask('8px', '8px', '8px'))}-webkit-mask-composite:source-in;mask-composite:intersect;`,
    now: `border-radius:12px;background-image:${featheredFill(0.55, 8)};background-size:100% 100%;background-repeat:no-repeat;`,
  },
  'tab bar': {
    box: 'left:0;top:20px;width:100%;height:64px;box-sizing:border-box;border:0 solid;border-top-width:1px;',
    old: `background:rgba(0,0,0,0.25);border-top-color:rgba(31,41,55,0.5);${masked(verticalMask('4px'))}`,
    now: `background-image:${verticalFill('rgba(0, 0, 0, 0.25)', '4px')};background-origin:border-box;border-top-color:rgba(31, 41, 55, 0.043);`,
  },
  'side rail': {
    box: 'left:0;top:20px;width:72px;height:320px;box-sizing:border-box;border:0 solid;border-right-width:1px;',
    old: `background:rgba(0,0,0,0.25);border-right-color:rgba(31,41,55,0.5);${masked(verticalMask('4px'))}`,
    now: `background-image:${verticalFill('rgba(0, 0, 0, 0.25)', '4px')};background-origin:border-box;border-right-color:rgba(31,41,55,0.5);`,
  },
}

const p = await page()
await p.send('Page.enable')
const dpr = await p.evaluate('devicePixelRatio')
await p.evaluate(`(() => {
  const overlay = document.createElement('div')
  overlay.id = 'bench-fill'
  overlay.style.cssText = 'position:fixed;inset:0;z-index:999999;background:' + (${JSON.stringify(process.argv[3] === 'dark')} ? 'repeating-linear-gradient(115deg,#1a1626 0 9px,#0c1420 9px 17px,#2b2b33 17px 23px,#241018 23px 36px),#111;' : 'repeating-linear-gradient(115deg,#d9c36a 0 9px,#3f7fd0 9px 17px,#f2f2f2 17px 23px,#b03a5b 23px 36px),#888;')
  overlay.innerHTML = '<div id="bench-fill-box" style="position:absolute"></div>'
  document.body.appendChild(overlay)
  return 1
})()`)
const shots = []
try {
  for (const [label, { box, old, now }] of Object.entries(CASES)) {
    for (const [version, css] of [['old', old], ['new', now]]) {
      await p.evaluate(`document.getElementById('bench-fill-box').style.cssText = ${JSON.stringify(`position:absolute;${box}${css}`)}, 1`)
      await sleep(350)
      const { result } = await p.send('Page.captureScreenshot', { format: 'png', clip: { x: 0, y: 0, width: 375, height: 360, scale: 1 }, captureBeyondViewport: false })
      const file = join(outDir, `${label.replace(/[^a-z0-9]+/gi, '_')}_${version}.png`)
      writeFileSync(file, Buffer.from(result.data, 'base64'))
      shots.push(file)
    }
  }
} finally {
  await p.evaluate(`document.getElementById('bench-fill')?.remove(), 1`)
}
console.log(`device pixel ratio ${dpr}; saved ${shots.length} screenshots in ${outDir}`)
p.close()
process.exit(0)
