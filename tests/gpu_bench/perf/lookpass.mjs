// node lookpass.mjs [panel] [rounds] [extra] [looks] — ms per main scene pass for each background look (slope method),
// with the colour split forced on (30 px) and off. Every shot of the song's plan is given the look of the first shot
// that has it; the plan is put back at the end. Works on any build that has the looks (15 Nov 2026 onwards).
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Playing'
const rounds = Number(process.argv[3] || 2)
const extra = Number(process.argv[4] || 6)
const looks = (process.argv[5] || 'cover,split,depth').split(',')

const p = await page()
const run = code => p.evaluate(`window.__plairScene.eval(${JSON.stringify(code)})`)
const force = look => run(`
  const plan = scene.plan
  if (!plan?.song) return 'no plan for this song'
  if (scene.__lookPlan !== plan) {
    scene.__lookPlan = plan
    scene.__lookShots = plan.shots.map(s => ({ look: s.look, variant: s.variant }))
  }
  const look = ${JSON.stringify(look)}
  if (!look) { plan.shots.forEach((s, i) => Object.assign(s, scene.__lookShots[i])); return 'ok' }
  const model = scene.__lookShots.find(s => s.look === look)
  if (!model) return 'no shot with the look ' + look
  plan.shots.forEach(s => { s.look = model.look; s.variant = model.variant })
  return 'ok'
`)
const measureOnce = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = ms => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])

if (panel !== 'none') console.log('panel:', await openPanel(p, panel))
await sleep(1500)
console.log(await run(`return JSON.stringify({ look: scene.look, quality: scene.state.visualQuality, depth: !!scene.depthGrid, canvas: [scene.gl.drawingBufferWidth, scene.gl.drawingBufferHeight], panels: scene.fgMaterial.uniforms.u_panel_count.value, interior: scene.splitHasInterior, outer: scene.splitHasOuter, backdrop: scene.splitHasBackdrop, playing: scene.state.isPlaying })`))
const results = {}
try {
  for (let r = 0; r < rounds; r++) {
    for (const look of looks) {
      for (const chroma of [30, 0]) {
        const name = `${look} split ${chroma ? 'on' : 'off'}`
        const forced = await force(look)
        if (forced !== 'ok') { console.log(name, forced); continue }
        const times = []
        for (const n of [extra, 2 * extra]) {
          await p.evaluate(`window.__plairScene.set({ force: true, extra: ${n}, uniforms: { u_chromatic: ${chroma} } }), 1`)
          await sleep(500)
          let last = await measure(700)
          for (let settle = 0; settle < 6; settle++) {
            const next = await measure(700)
            if (Math.abs(next - last) / next < 0.08) break
            last = next
          }
          times.push(await measure(2500))
        }
        const perPass = (times[1] - times[0]) / extra
        ;(results[name] ||= []).push(perPass)
        console.log(`round ${r + 1} ${name.padEnd(16)} +${extra} passes ${times[0]} ms, +${2 * extra} passes ${times[1]} ms -> ${perPass.toFixed(2)} ms/pass (${await run('return scene.look')})`)
      }
    }
  }
} finally {
  await force(null)
  await p.evaluate(`window.__plairScene.set({ force: false, extra: 0, uniforms: null }), 1`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
for (const [name, values] of Object.entries(results)) console.log(`${panel}: ${name.padEnd(16)} median ${median(values).toFixed(2)} ms/pass   runs ${values.map(v => v.toFixed(2)).join(', ')}`)
p.close()
process.exit(0)
