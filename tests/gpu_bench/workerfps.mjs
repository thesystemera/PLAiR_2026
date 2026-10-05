// node workerfps.mjs  — rAF rate on the phone untouched: main thread vs OffscreenCanvas in a worker
const sleep = ms => new Promise(r => setTimeout(r, ms))
function connect(url) { const s = new WebSocket(url); let id = 0; const pend = new Map(); s.addEventListener('message', e => { const m = JSON.parse(e.data); pend.get(m.id)?.(m) }); return { ready: new Promise(r => s.addEventListener('open', r, { once: true })), send: (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); s.send(JSON.stringify({ id: i, method, params })) }), close: () => s.close() } }
const html = `<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:100%;background:#000}canvas{position:fixed;inset:0;width:100%;height:100%}</style><canvas id=c></canvas><script>
const c=document.getElementById('c');c.width=innerWidth;c.height=innerHeight;const off=c.transferControlToOffscreen();
const src='let n=0,t0=0;onmessage=e=>{const cv=e.data.canvas;const gl=cv.getContext("webgl2");const f=t=>{if(!t0)t0=t;n++;gl.clearColor((n%60)/60,0.1,0.2,1);gl.clear(gl.COLOR_BUFFER_BIT);if(t-t0<4000)requestAnimationFrame(f);else postMessage(n/((t-t0)/1000))};requestAnimationFrame(f)}';
const w=new Worker(URL.createObjectURL(new Blob([src])));w.postMessage({canvas:off},[off]);
window.result=new Promise(r=>{w.onmessage=e=>r(e.data)});
let m=0,m0=0;const g=t=>{if(!m0)m0=t;m++;if(t-m0<4000)requestAnimationFrame(g);else window.mainFps=m/((t-m0)/1000)};requestAnimationFrame(g);
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
const r = await p.send('Runtime.evaluate', { awaitPromise: true, returnByValue: true, expression: 'window.result.then(w => new Promise(res => setTimeout(() => res(JSON.stringify({ workerFps: +w.toFixed(1), mainFps: +(window.mainFps || 0).toFixed(1) })), 300)))' })
console.log(r.result?.result?.value || JSON.stringify(r))
p.close(); await b.send('Target.closeTarget', { targetId: result.targetId }); b.close()
