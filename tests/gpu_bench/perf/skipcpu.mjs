// node skipcpu.mjs <skips> <label>=<port>,<page match> [<label>=<port>,<page match> ...] — main-thread CPU time in the
// 1.5 s after a press of Next, from a CPU profile, for one or more pages pressed in turn (old and new builds side by
// side in headless Chrome, or the phone alone: phone=9222,plair.live). Prints per page the mean and median of: busy
// (everything but idle), script (JavaScript), garbage collection and the rest (style, layout, paint: "(program)").
// CPU=4 slows each page's CPU. It changes the song once per skip on every page.
import { page, sleep, openPanel, closeDialogs } from './cdp.mjs'

const skips = Number(process.argv[2] || 6)
const targets = process.argv.slice(3).map(arg => {
  const [label, rest] = arg.split('=')
  const [port, match] = rest.split(',')
  return { label, port, match, runs: [] }
})
if (!targets.length) { console.log('usage: node skipcpu.mjs <skips> <label>=<port>,<page match> ...'); process.exit(1) }
const WINDOW_MS = 1500

for (const target of targets) {
  process.env.CDP_PORT = target.port
  target.page = await page(target.match)
  if (process.env.CPU) await target.page.send('Emulation.setCPUThrottlingRate', { rate: Number(process.env.CPU) })
  if (target.port !== '9222') await closeDialogs(target.page)
  await openPanel(target.page, process.env.PANEL || 'Playing')
  await target.page.send('Profiler.enable')
  await target.page.send('Profiler.setSamplingInterval', { interval: 200 })
}
await sleep(2000)

const press = async target => {
  const p = target.page
  await p.send('Profiler.start')
  const pressed = await p.evaluate(`(() => {
    const button = document.querySelector('button[title="Next track"]')
    if (!button) return false
    button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' }))
    return true
  })()`)
  await sleep(WINDOW_MS)
  const { result } = await p.send('Profiler.stop')
  if (!pressed) return null
  const profile = result.profile
  const self = new Map()
  for (let i = 0; i < profile.samples.length; i++) self.set(profile.samples[i], (self.get(profile.samples[i]) || 0) + (profile.timeDeltas[i] || 0) / 1000)
  const totals = { busy: 0, script: 0, gc: 0, rest: 0 }
  for (const node of profile.nodes) {
    const ms = self.get(node.id) || 0
    const name = node.callFrame.functionName
    if (name === '(idle)' || name === '(root)') continue
    totals.busy += ms
    if (name === '(garbage collector)') totals.gc += ms
    else if (name === '(program)') totals.rest += ms
    else totals.script += ms
  }
  return totals
}

for (let i = 0; i <= skips; i++) {
  for (const target of targets) {
    const run = await press(target)
    if (run && i > 0) target.runs.push(run)
    await sleep(2500)
  }
}

const mean = (runs, key) => Math.round(runs.reduce((sum, run) => sum + run[key], 0) / runs.length)
const median = (runs, key) => Math.round([...runs.map(run => run[key])].sort((a, b) => a - b)[Math.floor(runs.length / 2)])
for (const target of targets) {
  if (!target.runs.length) { console.log(`${target.label}: no Next button on screen`); continue }
  console.log(`${target.label.padEnd(8)} ${target.runs.length} skips, ms in the ${WINDOW_MS} ms after the press (mean / median): ` +
    ['busy', 'script', 'gc', 'rest'].map(key => `${key} ${mean(target.runs, key)} / ${median(target.runs, key)}`).join(', '))
  console.log(`${''.padEnd(8)} script per skip: ${target.runs.map(run => Math.round(run.script)).join(', ')}`)
  target.page.close()
}
process.exit(0)
