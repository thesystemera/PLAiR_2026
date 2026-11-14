// node sharebench.mjs '<json {label: options}>' — short share-video renders on the phone with the pipeline's bench
// switches (window.__plairShare.set): seconds (length of the test video), keepVisuals (the live scene keeps running),
// noEncode (render only), noRender (encode one still picture), encoder ({hardwareAcceleration, bitrateMode, ...}),
// keyFrameEvery. Nothing is uploaded while a bench option is set. Prints speed, bitrate and ms per frame.
import { page, sleep, openPanel } from './cdp.mjs'

const configs = JSON.parse(process.argv[2] || '{"as shipped":{"seconds":20}}')
const p = await page()
const clickText = text => p.evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().startsWith(${JSON.stringify(text)})); if (!b || b.disabled) return false; b.click(); return true })()`)
const until = async (expression, seconds, label) => {
  const end = Date.now() + seconds * 1000
  while (Date.now() < end) {
    const value = await p.evaluate(expression)
    if (value && !(typeof value === 'string' && value.startsWith('ERR'))) return value
    await sleep(300)
  }
  throw new Error(`timed out waiting for ${label}`)
}
try {
  await openPanel(p, 'Playing')
  for (const [label, options] of Object.entries(configs)) {
    await p.evaluate(`window.__plairShare.clear(), window.__plairShare.set(${JSON.stringify(options)}), window.__shareBefore = window.__plairShare.last(), 1`)
    await until(`(() => { const b = document.querySelector('button[title="Share track"]'); return !!b && !b.disabled })()`, 20, 'the Share button')
    await p.evaluate(`document.querySelector('button[title="Share track"]').click()`)
    await until(`[...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('Generate Video'))`, 10, 'the Share window')
    await sleep(700)
    await clickText('Generate Video')
    if (options.closeWindow) {
      await sleep(1200)
      await p.evaluate(`document.querySelector('button[aria-label="Close"]')?.click(), 1`)
    }
    const result = await until(`(() => {
      if (document.body.innerText.includes('Generation Failed')) return 'FAILED'
      const last = window.__plairShare.last()
      return last && last !== window.__shareBefore ? JSON.stringify(last) : ''
    })()`, 240, `the render (${label})`)
    console.log(label.padEnd(34), result)
    await sleep(1500)
    await p.evaluate(`window.__plairShare.clear(), 1`)
    await p.evaluate(`document.querySelector('button[aria-label="Close"]')?.click(), 1`)
    await sleep(1500)
  }
} finally {
  await p.evaluate(`window.__plairShare.set(null), window.__plairShare.clear(), 1`)
}
p.close()
process.exit(0)
