// node windowstate.mjs [tap] [wait ms] - reads the Catalog's paged track window (useVirtualWindow) from React's fibers on
// the phone: pages held, pages pending, the queue, the total, the sequence number and the last range the list asked for;
// with a sort button title as [tap] (Recent, Remastered, A-Z) it taps it first and reads after [wait]. Read-only otherwise.
import { page, sleep } from './cdp.mjs'

const tap = process.argv[2]
const wait = Number(process.argv[3] || 3000)
const p = await page()
const read = () => p.evaluate(`(() => {
  const button = document.querySelector('button[title="Recent"]')
  if (!button) return 'no catalog header'
  const fiberKey = Object.keys(button).find(k => k.startsWith('__reactFiber'))
  let fiber = button[fiberKey]
  const isDemand = v => v && typeof v === 'object' && 'focus' in v && 'args' in v && 'first' in v
  while (fiber) {
    let hook = fiber.memoizedState
    const refs = []
    while (hook && typeof hook === 'object' && 'next' in hook) {
      const state = hook.memoizedState
      if (state && typeof state === 'object' && 'current' in state && !('$$typeof' in state)) refs.push(state.current)
      hook = hook.next
    }
    const at = refs.findIndex(isDemand)
    if (at >= 0) {
      const pages = refs[at - 4], pending = refs[at - 3], failed = refs[at - 2], queue = refs[at - 1]
      const total = refs[at + 1], seq = refs[at + 2]
      const scroller = [...document.querySelectorAll('[data-scroller]')].find(s => s.contains(button))
      return JSON.stringify({
        scrollTop: Math.round(scroller.scrollTop),
        pages: pages instanceof Map ? [...pages.keys()].sort((a, b) => a - b) : String(pages),
        pending: pending instanceof Map ? [...pending.keys()] : String(pending),
        failed: failed instanceof Map ? [...failed.keys()] : String(failed),
        queue, total, seq, demand: refs[at],
        skeletons: scroller.querySelectorAll('.animate-pulse').length,
      })
    }
    fiber = fiber.return
  }
  return 'window hook not found'
})()`)
console.log('now   ', await read())
if (tap) {
  await p.evaluate(`document.querySelector('button[title="${tap}"]').click(), 1`)
  for (const ms of [150, 400, wait]) {
    await sleep(ms)
    console.log(`+${ms}ms`.padEnd(6), await read())
  }
}
p.close()
process.exit(0)
