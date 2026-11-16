// node renders3.mjs [panel] [seconds] — reloads the phone's page with a React commit recorder, presses Next once and
// prints every commit after the press: when, how many components re-rendered, and the roots of the cascades in it
// (the component a cascade started from, with its count). TYPES=1 adds the components of the biggest commit with the
// props that changed; TYPES=all sums them over every commit. It changes the owner's song and reloads the app.
import { page, sleep, openPanel } from './cdp.mjs'

const panel = process.argv[2] || 'Playing'
const seconds = Number(process.argv[3] || 2.5)
const HOOK = `(() => {
  const isComponent = f => f && (f.tag === 0 || f.tag === 1 || f.tag === 11 || f.tag === 14 || f.tag === 15)
  const typeOf = f => f.type?.type || f.type?.render || f.type
  const NAMES = [['onSeek:', 'Player'], ['onSeedRadio:', 'Queue'], ['onToggleFullscreen:', 'NowPlaying'], ['onPlayNow:', 'Catalog'], ['captureResolution', 'Scene']]
  const srcOf = f => { const text = String(typeOf(f)).slice(0, 90).replace(/\\s+/g, ' '); const known = NAMES.find(([mark]) => text.includes(mark)); return known ? known[1] : text.slice(0, 46) }
  const ownerOf = f => { let o = f.return; while (o && !isComponent(o)) o = o.return; return o }
  window.__commits = []
  window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
    supportsFiber: true, renderers: new Map(), isDisabled: false,
    inject() { return 1 }, checkDCE() {}, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {},
    onCommitFiberRoot(id, root) {
      const previous = window.__lastFibers || new Set()
      const seen = new Set()
      const roots = {}
      const types = {}
      let total = 0
      const stack = [root.current]
      const rendered = f => isComponent(f) && (f.flags & 1) && !previous.has(f)
      while (stack.length) {
        const f = stack.pop()
        seen.add(f)
        if (window.__commitWatch && rendered(f)) {
          total++
          let top = f
          let parent = ownerOf(f)
          while (parent && rendered(parent)) { top = parent; parent = ownerOf(parent) }
          const owner = ownerOf(top)
          const key = srcOf(top) + (owner ? ' < ' + srcOf(owner) : '')
          roots[key] = (roots[key] || 0) + 1
          const before = f.alternate?.memoizedProps
          const now = f.memoizedProps || {}
          const changed = before ? Object.keys(now).filter(k => now[k] !== before[k]) : ['(mounted)']
          const type = srcOf(f) + ' IN ' + srcOf(top) + '  props changed: ' + (changed.join(',') || 'none')
          types[type] = (types[type] || 0) + 1
        }
        if (f.child) stack.push(f.child)
        if (f.sibling) stack.push(f.sibling)
      }
      window.__lastFibers = seen
      if (window.__commitWatch && total) window.__commits.push({ t: performance.now(), total, roots, types })
    },
  }
})()`
const p = await page()
await p.send('Page.enable')
const added = await p.send('Page.addScriptToEvaluateOnNewDocument', { source: HOOK })
await p.send('Page.reload', { ignoreCache: false })
await sleep(11000)
await openPanel(p, panel)
await sleep(2500)
const out = JSON.parse(await p.evaluate(`(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  window.__commits.length = 0
  window.__commitWatch = true
  const pressed = performance.now()
  document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' }))
  await sleep(${seconds * 1000})
  window.__commitWatch = false
  return JSON.stringify(window.__commits.map(c => ({ at: Math.round(c.t - pressed), total: c.total, roots: Object.entries(c.roots).sort((a, b) => b[1] - a[1]).slice(0, 6), types: Object.entries(c.types).sort((a, b) => b[1] - a[1]).slice(0, 400) })))
})()`))
let total = 0
for (const commit of out) {
  total += commit.total
  console.log(`${String(commit.at).padStart(5)} ms: ${String(commit.total).padStart(4)} renders: ${commit.roots.map(([key, n]) => `${n} ${key}`).join(' | ')}`)
}
console.log(`${out.length} commits, ${total} component renders in ${seconds} s`)
if (process.env.TYPES) {
  const biggest = [...out].sort((a, b) => b.total - a.total)[0]
  console.log(`components in the biggest commit (${biggest.at} ms, ${biggest.total} renders):`)
  for (const [key, n] of biggest.types.slice(0, 60)) console.log(`  ${String(n).padStart(3)}  ${key}`)
}
if (process.env.TYPES === 'all') {
  const totals = {}
  for (const commit of out) for (const [key, n] of commit.types) totals[key] = (totals[key] || 0) + n
  console.log('components over all commits:')
  for (const [key, n] of Object.entries(totals).sort((a, b) => b[1] - a[1]).slice(0, 90)) console.log(`  ${String(n).padStart(3)}  ${key}`)
}
await p.send('Page.removeScriptToEvaluateOnNewDocument', { identifier: added.result?.identifier })
await p.send('Page.reload', { ignoreCache: false })
p.close()
process.exit(0)
