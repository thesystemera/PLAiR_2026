// node sharevideo_phone.mjs [close|stay] — renders a share video for the song on air on the phone and reports how long
// it took, what the page said and how the app ran meanwhile. `close` (default) shuts the Share window a few seconds in
// and waits for the "Video ready" notice, then opens the video from it. Screenshots go to SCRATCH (on E:).
// The video is published under the signed-in account, so only run it when the owner asked.
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { page, sleep, openPanel } from './cdp.mjs'

const mode = process.argv[2] || 'close'
const SCRATCH = process.env.SCRATCH || 'E:/scratch/plair/sharevideo_phone'
const TIMEOUT_S = Number(process.env.TIMEOUT_S || 1500)
mkdirSync(SCRATCH, { recursive: true })

const p = await page()
p.on(m => {
  if (m.method === 'Runtime.consoleAPICalled') {
    const text = m.params.args.map(a => a.value ?? (a.preview ? JSON.stringify(Object.fromEntries(a.preview.properties.map(v => [v.name, v.value]))) : a.description) ?? '').join(' ')
    if (/ShareVideo|OfflineVideoRenderer|ShareModal|\[ERROR\]|\[CRITICAL\]/.test(text)) console.log('  page:', text.slice(0, 500))
  }
  if (m.method === 'Runtime.exceptionThrown') console.log('  page exception:', JSON.stringify(m.params.exceptionDetails).slice(0, 500))
})
await p.send('Runtime.enable')
const until = async (expression, seconds, label) => {
  const end = Date.now() + seconds * 1000
  while (Date.now() < end) {
    const value = await p.evaluate(expression)
    if (value && !(typeof value === 'string' && value.startsWith('ERR'))) return value
    await sleep(300)
  }
  throw new Error(`timed out waiting for ${label}`)
}
const clickText = text => p.evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(${JSON.stringify(text)})); if (!b || b.disabled) return false; b.click(); return true })()`)
const shot = async name => {
  const out = await p.send('Page.captureScreenshot', { format: 'jpeg', quality: 70 })
  if (out.result?.data) writeFileSync(join(SCRATCH, `${name}.jpg`), Buffer.from(out.result.data, 'base64'))
}
const fps = ms => p.evaluate(`new Promise(resolve => { const f = []; const end = performance.now() + ${ms}; const tick = t => { f.push(t); if (t < end) requestAnimationFrame(tick); else { const gaps = f.slice(1).map((v, i) => v - f[i]); resolve(JSON.stringify({ fps: +((f.length - 1) / (f.at(-1) - f[0]) * 1000).toFixed(1), worstMs: +Math.max(...gaps).toFixed(0) })) } }; requestAnimationFrame(tick) })`)

let failed = null
try {
  await openPanel(p, 'Playing')
  await until(`(() => { const b = document.querySelector('button[title="Share track"]'); return !!b && !b.disabled })()`, 20, 'the Share button on Now Playing')
  console.log('song:', await p.evaluate(`navigator.mediaSession.metadata?.title || ''`), '| before:', await fps(3000))
  await p.evaluate(`document.querySelector('button[title="Share track"]').click()`)
  await until(`[...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('Generate Video'))`, 10, 'the Share window')
  await sleep(800)
  await shot('1-share-window')
  const started = Date.now()
  await clickText('Generate Video')
  console.log('render started')

  const ready = `!!document.querySelector('video[src^="blob:"]')`
  const failedText = `document.body.innerText.includes('Generation Failed')`
  const noticeReady = `document.body.innerText.includes('Video ready')`

  await sleep(5000)
  await shot('2-rendering')
  if (mode === 'close') {
    console.log('Share window closed:', await p.evaluate(`(() => { const b = document.querySelector('button[aria-label="Close"]'); if (!b) return 'no close button'; b.click(); return 'clicked' })()`))
    await sleep(1500)
    await shot('3-closed-while-rendering')
  }
  let last = -1
  let lastFpsAt = 0
  const end = Date.now() + TIMEOUT_S * 1000
  for (;;) {
    if (await p.evaluate(mode === 'close' ? noticeReady : ready)) break
    if (await p.evaluate(failedText)) throw new Error('the page says Generation Failed')
    if (Date.now() > end) throw new Error('render timed out')
    const percent = Number(await p.evaluate(`(document.body.innerText.match(/(?:^|\\n)(\\d{1,3})%(?:\\n|$)/) || [])[1] || '-1'`))
    if (percent >= 0 && percent !== last && percent >= last + 10) {
      last = percent
      console.log(`  ${percent}% at ${((Date.now() - started) / 1000).toFixed(0)} s`)
    }
    if (Date.now() - lastFpsAt > 30000) {
      lastFpsAt = Date.now()
      console.log(`  app while rendering (${((Date.now() - started) / 1000).toFixed(0)} s):`, await fps(3000))
      if (lastFpsAt - started < 40000) await shot('4-app-while-rendering')
    }
    await sleep(1000)
  }
  console.log(`video ready after ${((Date.now() - started) / 1000).toFixed(1)} s`)
  await shot('5-ready')
  if (mode === 'close') {
    console.log('Open clicked:', await clickText('Open'))
    await until(ready, 15, 'the finished video in the Share window')
    await sleep(1000)
    await shot('6-reopened')
  }
  console.log('video:', await p.evaluate(`(async () => {
    const v = document.querySelector('video[src^="blob:"]')
    if (v.readyState < 1) await new Promise(r => v.addEventListener('loadedmetadata', r, { once: true }))
    const label = (document.body.innerText.match(/(Hosted MP4 ready|MP4 ready) - [\\d.]+ MB/) || [])[0] || ''
    return JSON.stringify({ seconds: +v.duration.toFixed(1), width: v.videoWidth, height: v.videoHeight, label })
  })()`))
  console.log('after:', await fps(3000))
} catch (error) {
  failed = error
  console.log('FAILED:', error.message)
  try { await shot('failed') } catch { /* no page */ }
}
p.close()
process.exit(failed ? 1 : 0)
