// node tab.mjs open <url> | front <url part> | close <url part> | list — tabs in the phone's Chrome over USB. `open`
// starts a new tab in front (the owner's PLAiR tab moves to the background and keeps playing), `front` brings a tab
// forward, `close` closes only tabs whose address contains the given text.
const [command = 'list', value = ''] = process.argv.slice(2)
const version = await (await fetch('http://127.0.0.1:9222/json/version')).json()
const socket = new WebSocket(version.webSocketDebuggerUrl)
await new Promise(r => socket.addEventListener('open', r, { once: true }))
let id = 0
const pending = new Map()
socket.addEventListener('message', e => {
  const m = JSON.parse(e.data)
  if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
})
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); socket.send(JSON.stringify({ id: i, method, params })) })
const pages = async () => (await send('Target.getTargets')).result.targetInfos.filter(t => t.type === 'page')

if (command === 'open') {
  const made = await send('Target.createTarget', { url: value })
  console.log(made.result?.targetId ? `opened ${value}` : `could not open: ${JSON.stringify(made.error || made)}`)
} else if (command === 'front' || command === 'close') {
  if (!value) throw new Error('give part of the tab address')
  for (const target of (await pages()).filter(t => t.url.includes(value))) {
    await send(command === 'front' ? 'Target.activateTarget' : 'Target.closeTarget', { targetId: target.targetId })
    console.log(`${command === 'front' ? 'brought forward' : 'closed'} ${target.url}`)
  }
}
for (const target of await pages()) console.log('tab:', target.url.slice(0, 80))
socket.close()
process.exit(0)
