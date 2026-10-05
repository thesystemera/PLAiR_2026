import { page, sleep } from './cdp.mjs'
const p = await page()
const events = []
let done
const finished = new Promise(r => { done = r })
p.on(m => { if (m.method === 'Tracing.dataCollected') for (const e of m.params.value) if (e.ph === 'X' || e.ph === 'M' || (e.ph === 'R' || e.ph === 'I' || e.ph === 'n')) events.push(e); if (m.method === 'Tracing.tracingComplete') done() })
await p.send('Tracing.start', { categories: 'toplevel,blink,blink.user_timing,v8,v8.execute,disabled-by-default-devtools.timeline,gpu,viz,cc', transferMode: 'ReportEvents' })
await sleep(300)
await p.evaluate(`(() => { performance.mark('skip'); document.querySelector('button[title="Next track"]').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, isPrimary: true, pointerType: 'touch' })); return 1 })()`)
await sleep(1200)
await p.send('Tracing.end')
await finished
const threads = {}
for (const e of events) if (e.ph === 'M' && e.name === 'thread_name') threads[e.pid + ':' + e.tid] = e.args.name
const mark = events.find(e => e.name === 'skip')
const t0 = mark ? mark.ts : Math.min(...events.filter(e => e.ts).slice(0, 1000).map(e => e.ts))
const win = [t0, t0 + 1000e3]
const inWin = e => e.ts >= win[0] && e.ts < win[1]
const busy = th => Math.round(events.filter(e => e.ph === 'X' && threads[e.pid + ':' + e.tid] === th && (e.name === 'ThreadControllerImpl::RunTask' || e.name === 'ThreadPool_RunTask') && inWin(e)).reduce((a, e) => a + e.dur, 0) / 1000)
console.log('mark found', !!mark, '| busy ms in first second: main', busy('CrRendererMain'), 'gpu', busy('CrGpuMain'), 'viz', busy('VizCompositorThread'), 'compositor', busy('Compositor'), 'gpuCompositor', busy('CompositorGpuThread'), 'raster', busy('CompositorTileWorker1'))
const agg = {}
for (const e of events) if (e.ph === 'X' && threads[e.pid + ':' + e.tid] === 'CrRendererMain' && inWin(e) && /^(v8\.callFunction|FunctionCall|V8\.GC.*|MinorGC|MajorGC|UpdateLayoutTree|Layout|LocalFrameView::performLayout|Paint|PrePaint|Layerize|Commit|PaintArtifactCompositor::Update|HitTest|ParseHTML|Decode Image|ImageDecodeTask|ScheduleStyleRecalculation|Document::UpdateStyleAndLayout|RunMicrotasks)$/.test(e.name)) agg[e.name] = (agg[e.name] || 0) + e.dur / 1000
console.log(Object.entries(agg).sort((a, b) => b[1] - a[1]).map(([k, v]) => Math.round(v) + 'ms ' + k).join(' | '))
const gpuAgg = {}
for (const e of events) if (e.ph === 'X' && threads[e.pid + ':' + e.tid] === 'CrGpuMain' && inWin(e) && e.dur > 300 && !/RunTask|ThreadController|Scheduler|GPUTask|ExecuteDeferred|Flush|PutChanged|OnAsyncFlush|PerformWork/.test(e.name)) gpuAgg[e.name] = (gpuAgg[e.name] || 0) + e.dur / 1000
console.log('GPU:', Object.entries(gpuAgg).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([k, v]) => Math.round(v) + 'ms ' + k).join(' | '))
p.close(); process.exit(0)
