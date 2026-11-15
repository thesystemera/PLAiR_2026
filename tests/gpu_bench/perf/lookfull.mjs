// node lookfull.mjs [rounds] [extra] [programs] — ms per pass of each scene program drawn alone over the whole screen
// (slope method), to compare with what the same program costs on its small share of the split screen. Programs:
// interior, cover.full, cover.backdrop, split.full, split.backdrop, depth.full, depth.backdrop. Colour split forced on.
import { page, sleep } from './cdp.mjs'

const rounds = Number(process.argv[2] || 2)
const extra = Number(process.argv[3] || 6)
const programs = (process.argv[4] || 'interior,cover.backdrop,cover.full,split.backdrop,split.full,depth.backdrop,depth.full').split(',')

const p = await page()
const run = code => p.evaluate(`window.__plairScene.eval(${JSON.stringify(code)})`)
const solo = name => run(`
  const name = ${JSON.stringify(name)}
  if (!scene.__soloRender) {
    scene.__soloRender = scene.renderer.render.bind(scene.renderer)
    scene.renderer.render = (target, camera) => {
      const material = scene.__solo
      if (target !== scene.mainScene || !material) return scene.__soloRender(target, camera)
      const meshes = [scene.glassMesh, scene.glassInteriorMesh, scene.glassButtonMesh, scene.glassBackdropMesh]
      for (const mesh of meshes) mesh.visible = false
      const mesh = scene.backdropMesh
      const was = mesh.material
      mesh.visible = true
      mesh.material = material
      scene.__soloRender(target, camera)
      mesh.material = was
    }
  }
  if (!name) { scene.__solo = null; return 'ok' }
  const [look, part] = name.split('.')
  scene.__solo = name === 'interior' ? scene.glassInteriorMaterial : scene.lookMaterials[look]?.[part]
  if (!scene.__solo) return 'unknown program ' + name
  const plan = scene.plan
  if (name !== 'interior' && plan?.song) {
    if (scene.__lookPlan !== plan) {
      scene.__lookPlan = plan
      scene.__lookShots = plan.shots.map(s => ({ look: s.look, variant: s.variant }))
    }
    const model = scene.__lookShots.find(s => s.look === look)
    if (!model) return 'no shot with the look ' + look
    plan.shots.forEach(s => { s.look = model.look; s.variant = model.variant })
  }
  return 'ok'
`)
const measureOnce = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = ms => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])

const results = {}
try {
  for (let r = 0; r < rounds; r++) {
    for (const name of programs) {
      const set = await solo(name)
      if (set !== 'ok') { console.log(name, set); continue }
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
      ;(results[name] ||= []).push(perPass)
      console.log(`round ${r + 1} ${name.padEnd(16)} +${extra} passes ${times[0]} ms, +${2 * extra} passes ${times[1]} ms -> ${perPass.toFixed(2)} ms/pass (${await run('return scene.look')})`)
    }
  }
} finally {
  await solo(null)
  await run(`if (scene.__lookPlan === scene.plan) scene.plan.shots.forEach((s, i) => Object.assign(s, scene.__lookShots[i])); return 'ok'`)
  await p.evaluate(`window.__plairScene.set({ force: false, extra: 0, uniforms: null }), 1`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
for (const [name, values] of Object.entries(results)) console.log(`whole screen: ${name.padEnd(16)} median ${median(values).toFixed(2)} ms/pass   runs ${values.map(v => v.toFixed(2)).join(', ')}`)
p.close()
process.exit(0)
