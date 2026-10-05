(() => {
  const isComponent = f => f && (f.tag === 0 || f.tag === 1 || f.tag === 11 || f.tag === 14 || f.tag === 15)
  const typeOf = f => f.type?.type || f.type?.render || f.type
  const srcOf = f => String(typeOf(f)).slice(0, 70).replace(/\s+/g, ' ')
  const FRAMER = /presenceAffectsLayout|layoutId:|getSnapshotBeforeUpdate|switchLayoutGroup|isPresent|iconNode|children:t,isPresent/
  const ownerOf = f => { let o = f.return; while (o && !isComponent(o)) o = o.return; return o }
  const appOwner = f => { let o = ownerOf(f); while (o && FRAMER.test(String(typeOf(o)))) o = ownerOf(o); return o }
  window.__renderLog = []
  window.__renderRoots = new Map()
  window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
    supportsFiber: true, renderers: new Map(), isDisabled: false,
    inject() { return 1 }, checkDCE() {}, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {},
    onCommitFiberRoot(id, root) {
      const previous = window.__lastFibers || new Set()
      const seen = new Set()
      let total = 0
      const stack = [root.current]
      const rendered = f => isComponent(f) && (f.flags & 1) && !previous.has(f)
      while (stack.length) {
        const f = stack.pop()
        seen.add(f)
        if (window.__renderWatch && rendered(f)) {
          total++
          let top = f
          let parent = ownerOf(f)
          while (parent && rendered(parent)) { top = parent; parent = ownerOf(parent) }
          const app = appOwner(top)
          const key = srcOf(top).slice(0, 50) + '  IN  ' + (app ? srcOf(app).slice(0, 50) : '-')
          window.__renderRoots.set(key, (window.__renderRoots.get(key) || 0) + 1)
        }
        if (f.child) stack.push(f.child)
        if (f.sibling) stack.push(f.sibling)
      }
      window.__lastFibers = seen
      if (window.__renderWatch) window.__renderLog.push({ t: Math.round(performance.now()), total })
    },
  }
})()
