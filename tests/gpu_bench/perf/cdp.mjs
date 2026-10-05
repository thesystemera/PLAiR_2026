export async function page(match = 'plair.live') {
  const list = await (await fetch('http://127.0.0.1:9222/json')).json()
  const target = list.find(p => p.type === 'page' && p.url.includes(match))
  if (!target) throw new Error('no plair tab')
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  let id = 0
  const pending = new Map()
  const listeners = []
  socket.addEventListener('message', e => {
    const m = JSON.parse(e.data)
    if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
    for (const l of listeners) l(m)
  })
  await new Promise(r => socket.addEventListener('open', r, { once: true }))
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (r.result?.exceptionDetails) return 'ERR ' + JSON.stringify(r.result.exceptionDetails).slice(0, 600)
    return r.result?.result?.value
  }
  return { send, evaluate, on: f => listeners.push(f), close: () => socket.close() }
}
export const sleep = ms => new Promise(r => setTimeout(r, ms))
