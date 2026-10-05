import { page, sleep } from './cdp.mjs'
const p = await page()
const speed = Number(process.argv[2] || 3000)
await p.evaluate(`(async () => { const b = [...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === 'Catalog'); const panel = document.querySelector('[data-shader-panel=catalog]'); if (Math.round(panel.getBoundingClientRect().left) !== 0) { b.click(); await new Promise(r => setTimeout(r, 1500)) } return 1 })()`)
await p.evaluate(`(() => {
  const panel = document.querySelector('[data-shader-panel=catalog]')
  const s = window.__cw = { frames: 0, samples: 0, skeleton: 0, placeholderNoPack: 0, decoding: 0, uploading: 0, readyNotDrawn: 0, drawn: 0, running: true }
  const tick = () => {
    s.frames++
    if (s.frames % 3 === 0) {
      s.samples++
      const r = panel.getBoundingClientRect()
      const onScreen = el => { const b = el.getBoundingClientRect(); return b.width > 40 && b.bottom > r.top + 60 && b.top < r.bottom - 10 }
      s.skeleton += [...panel.querySelectorAll('.animate-pulse')].filter(onScreen).length
      const blank = window.__plairArt.blank()
      s.placeholderNoPack += blank.noPack; s.decoding += blank.decoding; s.uploading += blank.uploading; s.readyNotDrawn += blank.readyNotDrawn; s.drawn += blank.onScreen - blank.blank
    }
    if (s.running) requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
  return 1
})()`)
for (let i = 0; i < 6; i++) { await p.send('Input.synthesizeScrollGesture', { x: 187, y: 420, yDistance: -3000, speed, gestureSourceType: 'touch' }); await sleep(300) }
await sleep(800)
console.log(await p.evaluate(`(() => { const s = window.__cw; s.running = false; const per = k => +(s[k] / s.samples).toFixed(2); return JSON.stringify({ samples: s.samples, perSampleOnScreen: { skeletonNoData: per('skeleton'), placeholderNoPack: per('placeholderNoPack'), decoding: per('decoding'), uploading: per('uploading'), readyNotDrawn: per('readyNotDrawn'), drawn: per('drawn') } }) })()`))
p.close(); process.exit(0)
