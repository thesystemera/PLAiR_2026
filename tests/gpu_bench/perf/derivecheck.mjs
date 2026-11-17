// node derivecheck.mjs [stage] - applies a stage that uses derived per-cover uniforms (edits.mjs `derive`) and prints, for
// each cover program, the uniforms the renderer set and the derived values the hook computed from them, so a broken
// hook shows as missing or zero values instead of as a fast, wrong picture. Puts everything back at the end.
import { page, sleep } from './cdp.mjs'
import { STAGES, installShaderEdits, applyStage, removeShaderEdits } from './edits.mjs'

const name = process.argv[2] || 'per cover uniforms'
const p = await page()
await installShaderEdits(p)
try {
  await applyStage(p, STAGES[name])
  await p.evaluate('window.__plairArt.set({ bench: { forceFull: true, extraFull: 0, extraCopy: 0 } }), 1')
  await sleep(1200)
  console.log(await p.evaluate(`(() => {
    const seen = window.__deriveSeen || []
    return JSON.stringify(seen.slice(-3), (k, v) => (typeof v === 'number' ? +v.toFixed(4) : v))
  })()`))
} finally {
  await p.evaluate('window.__plairArt.set({ bench: { forceFull: false, extraFull: 0, extraCopy: 0 } }), 1')
  await removeShaderEdits(p)
}
p.close()
process.exit(0)
