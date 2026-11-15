// node lookstages.mjs <program> '<json {label: [[from, to], ...]}>' [rounds] [extra] ['<json pinned uniforms>'] — one scene
// program (interior, cover.backdrop, split.full, ...) drawn alone over the whole screen with its fragment source edited
// live, ms per pass by the slope method, rounds interleaved. `{"as is": []}` is always measured; an edit that matches
// nothing stops the run. Number uniforms can be pinned so the camera and focus do not move between variants.
import { page, sleep } from './cdp.mjs'

const program = process.argv[2] || 'cover.backdrop'
const variants = { 'as is': [], ...JSON.parse(process.argv[3] || '{}') }
const rounds = Number(process.argv[4] || 2)
const extra = Number(process.argv[5] || 6)
const pinned = { u_chromatic: 30, ...JSON.parse(process.argv[6] || '{}') }

const p = await page()
const run = code => p.evaluate(`window.__plairScene.eval(${JSON.stringify(code)})`)
const apply = edits => run(`
  const name = ${JSON.stringify(program)}
  const edits = ${JSON.stringify(edits)}
  if (!scene.__soloRender) {
    scene.__soloRender = scene.renderer.render.bind(scene.renderer)
    scene.renderer.render = (target, camera) => {
      const material = scene.__solo
      if (target !== scene.mainScene || !material) return scene.__soloRender(target, camera)
      for (const mesh of [scene.glassMesh, scene.glassInteriorMesh, scene.glassButtonMesh, scene.glassBackdropMesh]) mesh.visible = false
      const mesh = scene.backdropMesh
      const was = mesh.material
      mesh.visible = true
      mesh.material = material
      scene.__soloRender(target, camera)
      mesh.material = was
    }
  }
  const [look, part] = name.split('.')
  const material = name === 'interior' ? scene.glassInteriorMaterial : scene.lookMaterials[look]?.[part]
  if (!material) return 'unknown program ' + name
  scene.__stageSources = scene.__stageSources || new Map()
  if (!scene.__stageSources.has(material)) scene.__stageSources.set(material, material.fragmentShader)
  let source = scene.__stageSources.get(material)
  if (edits) {
    for (const [from, to] of edits) {
      if (!source.includes(from)) return 'edit matched nothing: ' + from.slice(0, 70)
      source = source.split(from).join(to)
    }
  }
  if (material.fragmentShader !== source) { material.fragmentShader = source; material.needsUpdate = true }
  scene.__solo = edits ? material : null
  const plan = scene.plan
  if (edits && name !== 'interior' && plan?.song) {
    if (scene.__lookPlan !== plan) {
      scene.__lookPlan = plan
      scene.__lookShots = plan.shots.map(s => ({ look: s.look, variant: s.variant }))
    }
    const model = scene.__lookShots.find(s => s.look === look)
    if (!model) return 'no shot with the look ' + look
    plan.shots.forEach(s => { s.look = model.look; s.variant = model.variant })
  }
  if (!edits && scene.__lookPlan === plan) plan.shots.forEach((s, i) => Object.assign(s, scene.__lookShots[i]))
  return 'ok'
`)
const measureOnce = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = ms => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])

const results = {}
try {
  for (let r = 0; r < rounds; r++) {
    for (const [name, edits] of Object.entries(variants)) {
      const set = await apply(edits)
      if (set !== 'ok') throw new Error(`${name}: ${set}`)
      const times = []
      for (const n of [extra, 2 * extra]) {
        await p.evaluate(`window.__plairScene.set({ force: true, extra: ${n}, uniforms: ${JSON.stringify(pinned)} }), 1`)
        await sleep(700)
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
      console.log(`round ${r + 1} ${name.padEnd(18)} +${extra} passes ${times[0]} ms, +${2 * extra} passes ${times[1]} ms -> ${perPass.toFixed(2)} ms/pass`)
    }
  }
} finally {
  await apply(null)
  await p.evaluate(`window.__plairScene.set({ force: false, extra: 0, uniforms: null }), 1`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const base = median(results['as is'])
for (const [name, values] of Object.entries(results)) {
  const m = median(values)
  console.log(`${program}: ${name.padEnd(18)} median ${m.toFixed(2)} ms/pass ${name === 'as is' ? '' : `(${(m - base >= 0 ? '+' : '') + (m - base).toFixed(2)})`}  runs ${values.map(v => v.toFixed(2)).join(', ')}`)
}
p.close()
process.exit(0)
