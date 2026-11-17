// node covershot.mjs <out dir> [scrollTop] [stage] - captures the on-screen Catalog covers' own canvases with everything
// pinned (Catalog at a fixed scroll position, fixed tilt, the four fixed test lights of the 'L4 const' stages at a fixed
// level, no kick or pulse; default stage 'L4 const coarse'), saving each cover as <out>/<track id>.png plus .rgba (raw
// pixels). Run it on two builds and compare with `python covershot_diff.py <dir a> <dir b>`: a renderer change that
// should not change the picture must come out identical.
import { mkdirSync, writeFileSync } from 'node:fs'
import { page, sleep, openPanel } from './cdp.mjs'
import { STAGES, installShaderEdits, applyStage, removeShaderEdits } from './edits.mjs'

const out = process.argv[2]
const scrollTop = Number(process.argv[3] || 2000)
const stageName = process.argv[4] || 'L4 const coarse'
if (!out) throw new Error('usage: node covershot.mjs <out dir> [scrollTop] [stage]')
mkdirSync(out, { recursive: true })
const p = await page()
await openPanel(p, 'Catalog')
await p.evaluate(`(() => { const s = [...document.querySelectorAll('[data-scroller]')].find(x => x.querySelector('button[title="Recent"]')); s.scrollTop = ${scrollTop}; return 1 })()`)
await sleep(3000)
await installShaderEdits(p)
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await p.evaluate(`window.__plairLight.debug({ freeze: true, kick: 0, pulse: 0, level: 0.6 }), window.__plairArt.set({ bench: { forceFull: true, extraFull: 0, extraCopy: 0 }, tilt: { x: 0.6, y: 0.4 } }), 1`)
try {
  await applyStage(p, STAGES[stageName])
  await sleep(2500)
  const covers = JSON.parse(await p.evaluate(`(() => {
    const s = [...document.querySelectorAll('[data-scroller]')].find(x => x.querySelector('button[title="Recent"]'))
    const box = s.getBoundingClientRect()
    return JSON.stringify([...s.querySelectorAll('canvas[role=img]')].filter(c => { const r = c.getBoundingClientRect(); return r.top >= box.top + 60 && r.bottom <= box.bottom && c.width > 100 }).map(c => {
      const card = c.closest('[class*="group/card"]')
      const data = c.getContext('2d').getImageData(0, 0, c.width, c.height).data
      let bin = ''
      for (let i = 0; i < data.length; i += 8192) bin += String.fromCharCode.apply(null, data.subarray(i, i + 8192))
      return { title: card?.querySelector('h3')?.textContent.trim() || '?', width: c.width, height: c.height, rgba: btoa(bin), png: c.toDataURL('image/png') }
    }))
  })()`))
  for (const cover of covers) {
    const name = cover.title.replace(/[^a-z0-9]+/gi, '_').slice(0, 40)
    writeFileSync(`${out}/${name}.rgba`, Buffer.from(cover.rgba, 'base64'))
    writeFileSync(`${out}/${name}.png`, Buffer.from(cover.png.split(',')[1], 'base64'))
    writeFileSync(`${out}/${name}.json`, JSON.stringify({ title: cover.title, width: cover.width, height: cover.height }))
    console.log(`${cover.title} ${cover.width}x${cover.height}`)
  }
} finally {
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  await removeShaderEdits(p)
  await p.evaluate(`window.__plairArt.set({ bench: { forceFull: false, extraFull: 0, extraCopy: 0 }, tilt: null }), window.__plairLight.debug({ freeze: false, off: false, level: null, kick: null, pulse: null }), 1`)
}
p.close()
