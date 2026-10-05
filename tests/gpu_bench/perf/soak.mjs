import { page, sleep } from './cdp.mjs'
const p = await page()
const logs = []
await p.send('Runtime.enable'); await p.send('Page.enable')
p.on(m => { if (m.method === 'Runtime.consoleAPICalled' && ['error','warning'].includes(m.params.type)) { const t = m.params.args.map(a => a.value || a.description || '').join(' '); if (/WebGL|context|DepthArt|Scene|shader|lost/i.test(t)) logs.push(Math.round(performance.now() / 1000) + 's ' + t.slice(0, 200)) } })
await p.send('Page.reload', { ignoreCache: true })
await sleep(12000)
const state = () => p.evaluate(`(async () => JSON.stringify({ scene: await window.__plairScene?.eval('return { lost: scene.renderer?.getContext?.().isContextLost?.(), ready: scene.programsReady }'), artTextures: window.__plairArt?.stats().textureMB }))()`)
console.log('after reload', await state(), JSON.stringify(logs))
await p.evaluate(`[...document.querySelectorAll('[data-mobile-nav] button')].find(b => b.innerText.trim() === 'Playing').click(), 1`)
let wobbling = true
const wobble = (async () => { const t0 = Date.now(); while (wobbling) { const t = (Date.now() - t0) / 1000; await p.send('DeviceOrientation.setDeviceOrientationOverride', { alpha: 0, beta: 10 * Math.sin(t * Math.PI * 1.6), gamma: 10 * Math.sin(t * Math.PI * 1.2 + 1) }); await sleep(33) } })()
for (let i = 0; i < Number(process.argv[2] || 6); i++) {
  await sleep(10000)
  const fps = await p.evaluate(`new Promise(r => { const f = []; const end = performance.now() + 2000; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else r(+((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1)) }; requestAnimationFrame(tick) })`)
  console.log(`${(i + 1) * 12}s`, 'fps', fps, await state(), logs.length ? JSON.stringify(logs.splice(0)) : '')
}
wobbling = false; await wobble
await p.send('DeviceOrientation.clearDeviceOrientationOverride')
p.close(); process.exit(0)
