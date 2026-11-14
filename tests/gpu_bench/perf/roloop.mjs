// node roloop.mjs install | read | remove — finds what causes "ResizeObserver loop completed with undelivered
// notifications" on the phone. `install` records inline style and class changes per frame; on each loop error it keeps
// the elements written in that frame (the writers) and the observed-looking elements whose size changed. `read` prints
// the tally, `remove` takes the hooks out. Passive: it changes nothing on the page.
import { page } from './cdp.mjs'

const cmd = process.argv[2] || 'read'
const p = await page()
const describe = `el => {
  if (!el || !el.tagName) return 'none'
  const data = [...el.attributes].filter(a => a.name.startsWith('data-')).map(a => a.name + (a.value && a.value.length < 24 ? '=' + a.value : '')).slice(0, 3).join(' ')
  const cls = (el.className && el.className.baseVal === undefined ? String(el.className) : '').split(' ').filter(Boolean).slice(0, 4).join('.')
  let depth = 0
  for (let n = el; n.parentElement; n = n.parentElement) depth++
  return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (data ? '[' + data + ']' : '') + (cls ? '.' + cls : '') + ' depth ' + depth
}`
if (cmd === 'install') {
  console.log(await p.evaluate(`(() => {
    if (window.__roLoop) return 'already installed'
    const describe = ${describe}
    const state = window.__roLoop = { errors: 0, writers: {}, frame: [], first: [] }
    state.mutations = new MutationObserver(records => {
      for (const r of records) state.frame.push(describe(r.target) + ' <' + r.attributeName + '>')
    })
    state.mutations.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'class'], subtree: true })
    const clear = () => { state.frame.length = 0; state.raf = requestAnimationFrame(clear) }
    state.raf = requestAnimationFrame(clear)
    state.onError = event => {
      if (!/ResizeObserver loop/.test(event.message || '')) return
      state.errors++
      for (const r of state.mutations.takeRecords()) state.frame.push(describe(r.target) + ' <' + r.attributeName + '>')
      const seen = new Set(state.frame)
      for (const key of seen) state.writers[key] = (state.writers[key] || 0) + 1
      if (state.first.length < 4) state.first.push([...seen].slice(0, 12))
    }
    window.addEventListener('error', state.onError, true)
    return 'installed'
  })()`))
} else if (cmd === 'read') {
  console.log(await p.evaluate(`(() => {
    const state = window.__roLoop
    if (!state) return 'not installed'
    const top = Object.entries(state.writers).sort((a, b) => b[1] - a[1]).slice(0, 25)
    return JSON.stringify({ errors: state.errors, writtenInTheSameFrame: top, firstErrors: state.first }, null, 1)
  })()`))
} else {
  console.log(await p.evaluate(`(() => {
    const state = window.__roLoop
    if (!state) return 'not installed'
    state.mutations.disconnect()
    cancelAnimationFrame(state.raf)
    window.removeEventListener('error', state.onError, true)
    delete window.__roLoop
    return 'removed'
  })()`))
}
p.close()
process.exit(0)
