// node scenepass.mjs [panel] [rounds] [extra] [variants] — cost of one main scene pass per shader variant (slope
// method): the scene's two glass shaders are edited live, each variant measured at 0 and N extra passes.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Radio'
const rounds = Number(process.argv[3] || 2)
const extra = Number(process.argv[4] || 6)

const FLAT_MAIN = 'void main() { gl_FragColor = vec4(0.1, 0.1, 0.1, 1.0); }'
const CAPTURE_MAIN = 'void main() { gl_FragColor = vec4(texture2D(u_capture_texture, vUv).rgb * 0.3, 1.0); }'
const main = body => ({ main: body })
export const VARIANTS = {
  'as is': {},
  'interior flat': { interior: main(FLAT_MAIN) },
  'interior one read': { interior: main(CAPTURE_MAIN) },
  'outer flat': { outer: main(FLAT_MAIN) },
  'both flat': { interior: main(FLAT_MAIN), outer: main(FLAT_MAIN) },
}
const extraVariants = process.env.VARIANTS_FILE ? (await import(process.env.VARIANTS_FILE)).default : {}
Object.assign(VARIANTS, extraVariants)
const names = (process.argv[5] || Object.keys(VARIANTS).join(',')).split(',')

const p = await page()
const apply = variant => p.evaluate(`window.__plairScene.eval(${JSON.stringify(`
  const variant = ${JSON.stringify(variant)}
  scene.__original = scene.__original || { outer: scene.fgMaterial.fragmentShader, interior: scene.glassInteriorMaterial.fragmentShader }
  const build = (source, change) => {
    if (!change) return source
    let out = source
    if (change.main) out = out.slice(0, out.lastIndexOf('void main()')) + change.main
    for (const [from, to] of change.edits || []) {
      if (!out.includes(from)) throw new Error('edit matched nothing: ' + from.slice(0, 60))
      out = out.split(from).join(to)
    }
    return out
  }
  const set = (material, source) => { if (material.fragmentShader !== source) { material.fragmentShader = source; material.needsUpdate = true } }
  set(scene.fgMaterial, build(scene.__original.outer, variant.outer))
  set(scene.glassInteriorMaterial, build(scene.__original.interior, variant.interior))
  return 'ok'
`)})`)
const measureOnce = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else resolve(+((f.at(-1) - f[0]) / (f.length - 1)).toFixed(2)) }; requestAnimationFrame(tick) })`)
const measure = ms => Promise.race([measureOnce(ms), sleep(ms + 8000).then(() => { throw new Error('the page stopped drawing frames') })])

await openPanel(p, panel)
await sleep(1500)
const results = Object.fromEntries(names.map(n => [n, []]))
try {
  for (let r = 0; r < rounds; r++) {
    for (const name of names) {
      const applied = await apply(VARIANTS[name])
      if (applied !== 'ok') throw new Error(`${name}: ${applied}`)
      const times = []
      for (const n of [0, extra]) {
        await p.evaluate(`window.__plairScene.set({ force: true, extra: ${n} }), 1`)
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
      results[name].push(perPass)
      console.log(`round ${r + 1} ${name.padEnd(20)} frame ${times[0]} ms, +${extra} passes ${times[1]} ms -> ${perPass.toFixed(2)} ms/pass`)
    }
  }
} finally {
  await apply({})
  await p.evaluate(`window.__plairScene.set({ force: false, extra: 0 }), 1`)
}
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const base = median(results['as is'] || [NaN])
for (const [name, values] of Object.entries(results)) {
  const m = median(values)
  console.log(`${panel}: ${name.padEnd(20)} median ${m.toFixed(2)} ms/pass`, name === 'as is' ? '' : `(${(m - base >= 0 ? '+' : '') + (m - base).toFixed(2)})`, 'runs', values.map(v => v.toFixed(2)).join(', '))
}
p.close()
process.exit(0)
