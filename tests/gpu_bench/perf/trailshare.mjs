import { page, sleep, openPanel } from './cdp.mjs'
import { installShaderEdits, applyStage, removeShaderEdits } from './edits.mjs'

const p = await page()
await installShaderEdits(p)
await openPanel(p, process.env.PANEL || 'Playing')
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await p.evaluate('window.__plairArt.set({ bench: { forceFull: true, extraFull: 0, extraCopy: 0 }, tilt: { x: 0.6, y: 0.4 } }), 1')
try {
  await applyStage(p, { light: { off: true }, edits: [
    ['fillMix = trailMask * 0.85;', 'fillMix = 1.0; fillColor = vec4(1.0, 0.0, 1.0, 1.0);'],
    ['SHADE float foregroundness = smoothstep(', 'pomColor = vec4(0.0, 0.0, 0.0, 1.0); SHADE float foregroundness = smoothstep('],
    ['if (foregroundness <= 0.0) return pomColor;', 'if (foregroundness <= 0.0) return pomColor; pomColor = vec4(0.0, 1.0, 0.0, 1.0);'],
  ] })
  await sleep(1500)
  console.log(await p.evaluate(`(() => {
    const onScreen = c => { const r = c.getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight && c.style.opacity !== '0' }
    return JSON.stringify([...document.querySelectorAll('canvas[role=img]')].filter(onScreen).sort((a, b) => b.width - a.width).slice(0, 6).map(canvas => {
      const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
      let fill = 0, foreground = 0
      for (let i = 0; i < data.length; i += 4) {
        if (data[i] > 128 && data[i + 2] > 128) fill++
        else if (data[i + 1] > 128) foreground++
      }
      const pixels = data.length / 4
      return { size: canvas.width + 'x' + canvas.height, fillPercent: +(fill / pixels * 100).toFixed(1), foregroundNoFillPercent: +(foreground / pixels * 100).toFixed(1) }
    }))
  })()`))
} finally {
  await removeShaderEdits(p)
  await p.evaluate(`(() => { window.__plairArt.set({ bench: { forceFull: false, extraFull: 0, extraCopy: 0 }, tilt: null }); window.__plairLight.debug({ off: false }); return 1 })()`)
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
}
p.close()
process.exit(0)
