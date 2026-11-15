// node lookparts.mjs [panel] [rounds] [extra] [looks] — where one main scene pass goes, per background look (slope method):
// the pass as it is, then with the panel-interior shader, the full scene shader and the backdrop shader each drawn flat.
// The colour split is forced on (30 px). Shaders and the plan are put back at the end.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Playing'
const rounds = Number(process.argv[3] || 2)
const extra = Number(process.argv[4] || 6)
const looks = (process.argv[5] || 'cover,split,depth').split(',')
const PARTS = ['as is', 'interior', 'full', 'backdrop']

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
const flatten = (look, part) => run(`
  const FLAT = 'void main() { gl_FragColor = vec4(0.1, 0.1, 0.1, 1.0); }'
  const materials = { interior: scene.glassInteriorMaterial, full: scene.lookMaterials[${JSON.stringify(look)}].full, backdrop: scene.lookMaterials[${JSON.stringify(look)}].backdrop }
  scene.__partSources = scene.__partSources || new Map()
  for (const material of [scene.glassInteriorMaterial, ...Object.values(scene.lookMaterials).flatMap(set => [set.full, set.backdrop])]) {
    if (!scene.__partSources.has(material)) scene.__partSources.set(material, material.fragmentShader)
    const original = scene.__partSources.get(material)
    const wanted = material === materials[${JSON.stringify(part)}] ? original.slice(0, original.lastIndexOf('void main()')) + FLAT : original
    if (material.fragmentShader !== wanted) { material.fragmentShader = wanted; material.needsUpdate = true }
  }
  return 'ok'
`)
const measureOnce = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = ms => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])

console.log('panel:', await openPanel(p, panel))
await sleep(1500)
const results = {}
try {
  for (let r = 0; r < rounds; r++) {
    for (const look of looks) {
      const forced = await force(look)
      if (forced !== 'ok') { console.log(look, forced); continue }
      for (const part of PARTS) {
        await flatten(look, part)
        const times = []
        for (const n of [extra, 2 * extra]) {
          await p.evaluate(`window.__plairScene.set({ force: true, extra: ${n}, uniforms: { u_chromatic: 30 } }), 1`)
          await sleep(600)
          let last = await measure(700)
          for (let settle = 0; settle < 6; settle++) {
            const next = await measure(700)
            if (Math.abs(next - last) / next < 0.08) break
            last = next
          }
          times.push(await measure(2500))
        }
        const perPass = (times[1] - times[0]) / extra
        const name = `${look} ${part === 'as is' ? 'as is' : `${part} flat`}`
        ;(results[name] ||= []).push(perPass)
        console.log(`round ${r + 1} ${name.padEnd(22)} ${perPass.toFixed(2)} ms/pass (${await run('return scene.look')})`)
      }
    }
  }
} finally {
  await flatten('cover', 'none')
  await force(null)
  await p.evaluate(`window.__plairScene.set({ force: false, extra: 0, uniforms: null }), 1`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
for (const [name, values] of Object.entries(results)) console.log(`${panel}: ${name.padEnd(22)} median ${median(values).toFixed(2)} ms/pass   runs ${values.map(v => v.toFixed(2)).join(', ')}`)
p.close()
process.exit(0)
