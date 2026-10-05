// node canvastest.mjs '<json opts>' seconds  — bare full-screen WebGL canvas on the phone, GPU busy while it clears every frame
import { execFileSync } from 'node:child_process'
const ADB = process.env.ADB
const opts = JSON.parse(process.argv[2] || '{}')
const seconds = Number(process.argv[3] || 6)
const sleep = ms => new Promise(r => setTimeout(r, ms))
function connect(url) { const s = new WebSocket(url); let id = 0; const pend = new Map(); s.addEventListener('message', e => { const m = JSON.parse(e.data); pend.get(m.id)?.(m) }); return { ready: new Promise(r => s.addEventListener('open', r, { once: true })), send: (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); s.send(JSON.stringify({ id: i, method, params })) }), close: () => s.close() } }
const html = `<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:100%;background:#000;overflow:hidden}canvas{position:fixed;inset:0;width:100%;height:100%}</style><canvas id=c></canvas><script>
const o=${JSON.stringify(opts)};const c=document.getElementById('c');const d=(o.dpr||devicePixelRatio);c.width=Math.round(innerWidth*d);c.height=Math.round(innerHeight*d);
const gl=c.getContext('webgl2',{alpha:!!o.alpha,antialias:false,depth:false,stencil:false,desynchronized:!!o.desync,preserveDrawingBuffer:!!o.preserve,powerPreference:'high-performance'});
let t=0;const f=()=>{t++;gl.clearColor(0.2+0.1*Math.sin(t/20),0.1,0.2,1);gl.clear(gl.COLOR_BUFFER_BIT);gl.enable(gl.SCISSOR_TEST);gl.scissor((t*4)%c.width,c.height/2,c.width/6,c.height/6);gl.clearColor(1,1,0,1);gl.clear(gl.COLOR_BUFFER_BIT);gl.disable(gl.SCISSOR_TEST);requestAnimationFrame(f)};requestAnimationFrame(f);
${opts.overlay ? "for(let i=0;i<o.overlay;i++){const e=document.createElement('div');e.style.cssText='position:fixed;inset:0;background:rgba(255,255,255,0.03);will-change:transform;color:#0f0;font:40px sans-serif;padding:'+(40+i*60)+'px 20px';e.textContent='overlay '+i;document.body.appendChild(e)}" : ''}
</script>`
const v = await (await fetch('http://127.0.0.1:9222/json/version')).json()
const b = connect(v.webSocketDebuggerUrl); await b.ready
const { result } = await b.send('Target.createTarget', { url: 'about:blank' })
await sleep(800)
const t = (await (await fetch('http://127.0.0.1:9222/json')).json()).find(x => x.id === result.targetId)
const p = connect(t.webSocketDebuggerUrl); await p.ready
await p.send('Page.enable')
const fr = await p.send('Page.getFrameTree')
await p.send('Page.setDocumentContent', { frameId: fr.result.frameTree.frame.id, html })
await sleep(2500)
const samples = []; let last = ''; const end = Date.now() + seconds * 1000
while (Date.now() < end) { const raw = execFileSync(ADB, ['shell', 'cat /sys/class/kgsl/kgsl-3d0/gpubusy']).toString().trim(); if (raw !== last) { last = raw; const [bb, tt] = raw.split(/\s+/).map(Number); if (tt > 0) samples.push(bb / tt) } await sleep(250) }
samples.shift()
if (process.env.SHOT) { for (const k of [1, 2]) { execFileSync(ADB, ['shell', `screencap -p /sdcard/ct${k}.png`]); await sleep(300) } }
const info = await p.send('Runtime.evaluate', { returnByValue: true, expression: 'JSON.stringify({w:c.width,h:c.height})' })
console.log(`${JSON.stringify(opts).padEnd(40)} gpu busy ${(100 * samples.reduce((a, x) => a + x, 0) / samples.length).toFixed(1)}%  ${info.result.result.value}`)
p.close(); await b.send('Target.closeTarget', { targetId: result.targetId }); b.close()
