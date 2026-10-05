import { mkdirSync, writeFileSync } from 'node:fs'
import { page, sleep } from './cdp.mjs'
import { STAGES, installShaderEdits, applyStage, removeShaderEdits } from './edits.mjs'

const names = (process.argv[2] || 'as is,as is,start at bound').split(',')
const outDir = process.argv[3] || 'E:/AI_RADIO/data/perf_identity'
mkdirSync(outDir, { recursive: true })

const p = await page()
await installShaderEdits(p)
const panel = process.argv[4]
if (panel) await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === ${JSON.stringify(panel)}).click(), 1`)
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await sleep(2500)
const level = await p.evaluate('window.__plairLight.read().level')
await p.evaluate(`window.__plairLight.debug({ freeze: true, kick: 0, pulse: 0, level: ${Math.max(0.3, level)} }), window.__plairArt.set({ bench: { forceFull: true, extraFull: 0, extraCopy: 0 } }), window.__idShots = {}, 1`)
const rows = []
try {
  for (const [index, name] of names.entries()) {
    const stage = STAGES[name]
    await applyStage(p, stage)
    await sleep(1200)
    const shot = await p.evaluate(`(() => {
      const onScreen = c => { const r = c.getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight && c.style.opacity !== '0' }
      const canvas = [...document.querySelectorAll('canvas[role=img]')].filter(onScreen).sort((a, b) => b.width - a.width)[0]
      const ctx = canvas.getContext('2d')
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
      window.__idShots[${index}] = data
      const ref = window.__idShots[0]
      let sum = 0, max = 0, over2 = 0, over8 = 0
      const diff = new ImageData(canvas.width, canvas.height)
      for (let i = 0; i < data.length; i += 4) {
        let pixelMax = 0
        for (let c = 0; c < 3; c++) {
          const d = Math.abs(data[i + c] - ref[i + c])
          sum += d
          if (d > pixelMax) pixelMax = d
          diff.data[i + c] = Math.min(255, d * 8)
        }
        diff.data[i + 3] = 255
        if (pixelMax > max) max = pixelMax
        if (pixelMax > 2) over2++
        if (pixelMax > 8) over8++
      }
      const pixels = data.length / 4
      let lum = 0, sat = 0, white = 0, clipped = 0
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i], g = data[i + 1], b = data[i + 2]
        const hi = Math.max(r, g, b), lo = Math.min(r, g, b)
        const s = hi ? (hi - lo) / hi : 0
        lum += 0.2126 * r + 0.7152 * g + 0.0722 * b
        sat += s
        if (lo >= 215 && s < 0.15) white++
        if (hi === 255) clipped++
      }
      const look = { lum: +(lum / pixels).toFixed(1), sat: +(sat / pixels).toFixed(3), white: +(white / pixels * 100).toFixed(2), clipped: +(clipped / pixels * 100).toFixed(2) }
      const out = document.createElement('canvas')
      out.width = canvas.width
      out.height = canvas.height
      out.getContext('2d').putImageData(diff, 0, 0)
      return JSON.stringify({ look, size: canvas.width + 'x' + canvas.height, mean: +(sum / (pixels * 3)).toFixed(3), max, over2: +(over2 / pixels * 100).toFixed(3), over8: +(over8 / pixels * 100).toFixed(3), image: canvas.toDataURL('image/png'), diff: out.toDataURL('image/png') })
    })()`)
    const r = JSON.parse(shot)
    const file = `${index}_${name.replace(/\W+/g, '_')}`
    writeFileSync(`${outDir}/${file}.png`, Buffer.from(r.image.split(',')[1], 'base64'))
    if (index > 0) writeFileSync(`${outDir}/${file}_diff_x8.png`, Buffer.from(r.diff.split(',')[1], 'base64'))
    const row = `${String(index).padStart(2)} ${name.padEnd(18)} ${r.size} vs shot 0: mean ${r.mean}, max ${r.max}, pixels off by >2: ${r.over2}%, by >8: ${r.over8}% | brightness ${r.look.lum}, saturation ${r.look.sat}, near-white ${r.look.white}%, clipped ${r.look.clipped}%`
    rows.push(row)
    console.log(row)
  }
} finally {
  await removeShaderEdits(p)
  await p.evaluate(`window.__plairLight.debug({ off: false, freeze: false, kick: null, pulse: null, level: null }), window.__plairArt.set({ bench: { forceFull: false, extraFull: 0, extraCopy: 0 } }), delete window.__idShots, 1`)
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
}
writeFileSync(`${outDir}/results.txt`, rows.join('\n') + '\n')
p.close()
process.exit(0)
