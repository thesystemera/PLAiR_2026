// node handlers.mjs install|remove - test switches for the Catalog's scroll listeners in the live page, found through
// React's fibers: window.__handlers.offScroller() / onScroller() detach and re-attach the Scroller's scroll listener
// (scroll label, indicator, the shader's scroll effect), offList() / onList() run the cleanup and setup of the
// VirtualScroller's listener effect (rows, cover prefetch, page requests). For scrollab.mjs variants; no build.
import { page } from './cdp.mjs'

const p = await page()
const command = process.argv[2] || 'install'
console.log(await p.evaluate(command === 'remove' ? `(() => { window.__handlers?.onScroller(); window.__handlers?.onList(); delete window.__handlers; return 'removed' })()` : `(() => {
  const scroller = [...document.querySelectorAll('[data-scroller]')].find(s => s.querySelector('button[title="Recent"]'))
  if (!scroller) return 'no catalog list'
  const fiberOf = el => el[Object.keys(el).find(k => k.startsWith('__reactFiber'))]
  const hooksOf = fiber => { const out = []; let h = fiber.memoizedState; while (h && typeof h === 'object' && 'next' in h) { out.push(h); h = h.next } return out }
  let scrollerHandler = null
  for (let f = fiberOf(scroller); f && !scrollerHandler; f = f.return) {
    for (const h of hooksOf(f)) {
      const v = h.memoizedState
      const fn = Array.isArray(v) && typeof v[0] === 'function' ? v[0] : null
      if (fn && /autoScroll/.test(fn.toString())) { scrollerHandler = fn; break }
    }
  }
  let listEffect = null
  const win = scroller.querySelector('[data-virtual-window]')
  for (let f = fiberOf(win.parentElement); f && !listEffect; f = f.return) {
    for (const h of hooksOf(f)) {
      const e = h.memoizedState
      if (e && typeof e === 'object' && typeof e.create === 'function' && /scrollend/.test(e.create.toString())) { listEffect = e; break }
    }
  }
  let latestHook = null
  for (let f = fiberOf(win.parentElement); f && !latestHook; f = f.return) {
    for (const h of hooksOf(f)) {
      const r = h.memoizedState
      if (r && typeof r === 'object' && 'current' in r && r.current && typeof r.current === 'object' && 'onRangeChange' in r.current && 'layout' in r.current) { latestHook = r; break }
    }
  }
  if (!scrollerHandler || !listEffect || !latestHook) return 'not found: scroller ' + !!scrollerHandler + ', list ' + !!listEffect
  const destroyOf = e => e.destroy || e.inst?.destroy
  const setDestroy = (e, fn) => { if (e.inst) e.inst.destroy = fn; else e.destroy = fn }
  let scrollerOff = false, listOff = false
  window.__handlers = {
    offScroller: () => { if (!scrollerOff) { scroller.removeEventListener('scroll', scrollerHandler); scrollerOff = true } return 1 },
    onScroller: () => { if (scrollerOff) { scroller.addEventListener('scroll', scrollerHandler, { passive: true }); scrollerOff = false } return 1 },
    offList: () => { if (!listOff) { destroyOf(listEffect)?.(); listOff = true } return 1 },
    onList: () => { if (listOff) { setDestroy(listEffect, listEffect.create()); listOff = false } return 1 },
    offPrefetch: () => {
      let value = latestHook.current
      const strip = v => (v && v.getPrefetchId ? { ...v, getPrefetchId: null } : v)
      value = strip(value)
      Object.defineProperty(latestHook, 'current', { configurable: true, get: () => value, set: v => { value = strip(v) } })
      return 1
    },
    onPrefetch: () => { const value = latestHook.current; delete latestHook.current; latestHook.current = value; return 1 },
  }
  return 'installed'
})()`))
p.close()
process.exit(0)
