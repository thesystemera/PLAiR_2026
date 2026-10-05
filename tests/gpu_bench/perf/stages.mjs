import { page, sleep } from './cdp.mjs'
import { STAGES, installShaderEdits, applyStage, removeShaderEdits } from './edits.mjs'

const names = (process.argv[2] || Object.keys(STAGES).join(',')).split(',')
const rounds = Number(process.argv[3] || 2)
const extra = Number(process.argv[4] || 6)

const p = await page()
await installShaderEdits(p)
const measure = (ms) => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)

await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === 'Playing').click(), 1`)
await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 8, gamma: 6 })
await p.evaluate('window.__plairLight.debug({ level: 0.6, kick: 0, pulse: 0 }), 1')
await sleep(1500)

const results = Object.fromEntries(names.map(n => [n, []]))
try {
  for (let r = 0; r < rounds; r++) {
    for (const name of names) {
      const stage = STAGES[name]
      const hits = await applyStage(p, stage)
      const times = []
      for (const n of [0, extra]) {
        await p.evaluate(`window.__plairArt.set({ bench: { forceFull: true, extraFull: ${n}, extraCopy: 0 } }), 1`)
        await sleep(400)
        let last = await measure(700)
        for (let settle = 0; settle < 6; settle++) {
          const next = await measure(700)
          if (Number.isFinite(last) && Math.abs(next - last) / next < 0.08) break
          last = next
        }
        times.push(await measure(2500))
      }
      const perPass = (times[1] - times[0]) / extra
      results[name].push(perPass)
      console.log(`round ${r + 1} ${name.padEnd(16)} frame ${times[0]} ms, +${extra} passes ${times[1]} ms -> ${perPass.toFixed(2)} ms/pass (programs edited: ${hits})`)
    }
  }
} finally {
  await p.send('DeviceOrientation.clearDeviceOrientationOverride')
  await removeShaderEdits(p)
  await p.evaluate(`(() => { window.__plairArt.set({ bench: { forceFull: false, extraFull: 0, extraCopy: 0 } }); window.__plairLight.debug({ off: false, level: null, kick: null, pulse: null }); return 1 })()`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const base = median(results['as is'] || [NaN])
for (const [name, values] of Object.entries(results)) {
  const m = median(values)
  console.log(name.padEnd(16), 'median', m.toFixed(2), 'ms/pass', name === 'as is' ? '' : `(${(m - base >= 0 ? '+' : '') + (m - base).toFixed(2)} vs as is)`, 'runs', values.map(v => v.toFixed(2)).join(', '))
}
p.close()
process.exit(0)
