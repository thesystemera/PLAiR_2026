// node toggle.mjs [button title] [rounds] [panel] — how smoothly a player panel (Devices, stream quality) opens and
// closes on the phone. Clicks the button, records every frame for 900 ms, clicks again to close, repeats. Prints per
// round: frames drawn, the average and worst frame, time spent in frames over 25 ms, and React commits. The panel
// opens and closes on the phone while it runs. Default button: "Manage devices".
import { page, sleep, openPanel } from './cdp.mjs'

const title = process.argv[2] || 'Manage devices'
const rounds = Number(process.argv[3] || 3)
const panel = process.argv[4]
const p = await page()
if (panel) {
  await openPanel(p, panel)
  await sleep(2000)
}
const record = `(title, ms) => new Promise(resolve => {
  const button = [...document.querySelectorAll('button')].find(b => b.title === title || (b.title || '').startsWith(title))
  if (!button) return resolve(JSON.stringify({ error: 'button not found: ' + title }))
  const frames = []
  const long = []
  const observer = PerformanceObserver.supportedEntryTypes.includes('long-animation-frame') ? new PerformanceObserver(list => long.push(...list.getEntries().map(e => ({ d: Math.round(e.duration), s: (e.scripts || []).slice(0, 3).map(x => (x.sourceFunctionName || x.invoker || '').slice(0, 40) + ' ' + Math.round(x.duration)) })))) : null
  observer?.observe({ type: 'long-animation-frame' })
  let commits = 0
  const root = document.getElementById('root')
  const mutations = new MutationObserver(() => { commits++ })
  mutations.observe(root, { subtree: true, attributes: true, childList: true, attributeFilter: ['style', 'class'] })
  button.click()
  const start = performance.now()
  const tick = t => {
    frames.push(t)
    if (t - start < ms) return requestAnimationFrame(tick)
    observer?.disconnect()
    mutations.disconnect()
    const gaps = []
    for (let i = 1; i < frames.length; i++) gaps.push(frames[i] - frames[i - 1])
    resolve(JSON.stringify({
      frames: gaps.length,
      avg: +(gaps.reduce((a, b) => a + b, 0) / gaps.length).toFixed(1),
      worst: +Math.max(...gaps).toFixed(0),
      over25: gaps.filter(g => g > 25).length,
      slowMs: Math.round(gaps.filter(g => g > 25).reduce((a, b) => a + b, 0)),
      mutationBatches: commits,
      long: long.slice(0, 4),
    }))
  }
  requestAnimationFrame(tick)
})`
for (let round = 0; round < rounds; round++) {
  for (const action of ['open ', 'close']) {
    console.log(action, await p.evaluate(`(${record})(${JSON.stringify(title)}, 900)`))
    await sleep(700)
  }
}
p.close()
process.exit(0)
