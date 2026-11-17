// node scrollerstate.mjs [tap] [ms] - taps a Catalog sort button (default Remastered) and records, every animation frame for
// [ms] (default 2500), the Catalog list's VirtualScroller internals read from React's fibers (which instance is mounted,
// items it holds, window start, its last demand key and items version, its rendered range, the list's measured top) next to
// the paged window's state (pages, pending, last range asked for), the scroll position and whether the spinner is shown.
// Prints one line per change. Read-only apart from the tap.
import { page, sleep } from './cdp.mjs'

const tap = process.argv[2] || 'Remastered'
const ms = Number(process.argv[3] || 2500)
const p = await page()
const setup = await p.evaluate(`(() => {
  const refsOf = (fiber) => {
    const refs = []
    let hook = fiber.memoizedState
    while (hook && typeof hook === 'object' && 'next' in hook) {
      const s = hook.memoizedState
      if (s && typeof s === 'object' && 'current' in s && !('$$typeof' in s)) refs.push(s.current)
      hook = hook.next
    }
    return refs
  }
  const fiberOf = el => el && el[Object.keys(el).find(k => k.startsWith('__reactFiber'))]
  let ids = 0
  const read = () => {
    const button = document.querySelector('button[title="Recent"]') || document.querySelector('button[title="Remastered"]')
    const scroller = [...document.querySelectorAll('[data-scroller]')].find(s => s.contains(button))
    const out = { t: Math.round(performance.now() - window.__diagStart) }
    if (!scroller) return { ...out, scroller: 'none' }
    out.top = Math.round(scroller.scrollTop)
    out.height = scroller.scrollHeight
    out.spinner = !!scroller.querySelector('.animate-spin')
    const windows = [...scroller.querySelectorAll('[data-virtual-window]')]
    out.lists = windows.map(win => {
      const list = win.parentElement
      if (!list.__vsid) list.__vsid = ++ids + (window.__diagIds || 0)
      let fiber = fiberOf(list)
      while (fiber) {
        const refs = refsOf(fiber)
        const latest = refs.find(r => r && typeof r === 'object' && 'onRangeChange' in r && 'layout' in r)
        if (latest) {
          const demandKey = refs.find(r => typeof r === 'string')
          const range = refs.find(r => r && typeof r === 'object' && 'start' in r && 'end' in r && !('byIndex' in r))
          const motion = refs.find(r => r && typeof r === 'object' && 'listTop' in r)
          const version = refs.filter(r => typeof r === 'number')
          return { id: list.__vsid, items: latest.items.length, windowStart: latest.windowStart, rows: latest.layout.rows, rowH: Math.round(latest.layout.rowHeight), onRange: typeof latest.onRangeChange, demandKey, versions: version.join(','), range: range && range.start + '-' + range.end, listTop: motion && Math.round(motion.listTop), motionTop: motion && Math.round(motion.top), scrolling: motion && motion.scrolling, parentOpacity: getComputedStyle(list.closest('[data-scroller] > * > *') || list).opacity }
        }
        fiber = fiber.return
      }
      return { id: list.__vsid, fiber: 'not found' }
    })
    let fiber = fiberOf(button)
    const isDemand = v => v && typeof v === 'object' && 'focus' in v && 'args' in v && 'first' in v
    while (fiber) {
      const refs = refsOf(fiber)
      const at = refs.findIndex(isDemand)
      if (at >= 0) {
        const pages = refs[at - 4], pending = refs[at - 3]
        out.pages = [...pages.keys()].sort((a, b) => a - b).join(',')
        out.pending = [...pending.keys()].join(',')
        out.args = refs[at].args && refs[at].args.join(',')
        out.seq = refs[at + 2]
        break
      }
      fiber = fiber.return
    }
    return out
  }
  window.__diagRead = read
  return JSON.stringify(read())
})()`)
console.log('before', setup)
await p.evaluate(`(() => {
  window.__diagStart = performance.now()
  window.__diagLog = []
  let last = ''
  const step = () => {
    const now = performance.now() - window.__diagStart
    const state = window.__diagRead()
    const { t, ...rest } = state
    const key = JSON.stringify(rest)
    if (key !== last) { window.__diagLog.push(state); last = key }
    if (now < ${ms}) requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
  const b = document.querySelector('button[title="${tap}"]')
  if (b) b.click()
  return 1
})()`)
await sleep(ms + 300)
const log = JSON.parse(await p.evaluate('JSON.stringify(window.__diagLog)'))
for (const entry of log) console.log(JSON.stringify(entry))
p.close()
process.exit(0)
