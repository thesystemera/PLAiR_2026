// node insetsync.mjs [button title] — opens and closes a player panel and checks, frame by frame, that the panel area
// above the player keeps its distance to the player's top edge (the panels follow the player without lagging).
import { page, sleep } from './cdp.mjs'

const title = process.argv[2] || 'Click to change audio quality'
const p = await page()
for (const action of ['open', 'close']) {
  const out = await p.evaluate(`new Promise(resolve => {
    const player = document.querySelector('[data-shader-panel="player"] [data-shader-panel="player"]') || [...document.querySelectorAll('[data-shader-panel="player"]')].pop()
    const row = [...document.querySelectorAll('div.fixed')].find(el => el.querySelector('[data-shader-panel]') && !el.contains(player))
    const button = document.querySelector('button[title="${title}"]')
    if (!player || !row || !button) return resolve('missing: ' + [!!player, !!row, !!button].join(','))
    const gap = () => player.getBoundingClientRect().top - row.getBoundingClientRect().bottom
    const rest = gap()
    const tops = []
    const gaps = []
    button.click()
    let frames = 0
    const tick = () => {
      tops.push(player.getBoundingClientRect().top)
      gaps.push(gap())
      if (++frames < 50) requestAnimationFrame(tick)
      else resolve(JSON.stringify({ restGap: +rest.toFixed(1), playerMovedPx: +(Math.max(...tops) - Math.min(...tops)).toFixed(1), worstGapChangePx: +Math.max(...gaps.map(g => Math.abs(g - rest))).toFixed(1), framesOff: gaps.filter(g => Math.abs(g - rest) > 0.6).length }))
    }
    requestAnimationFrame(tick)
  })`)
  console.log(action.padEnd(6), out)
  await sleep(600)
}
p.close()
process.exit(0)
