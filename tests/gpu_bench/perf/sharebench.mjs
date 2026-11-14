// node sharebench.mjs '<json {label: options}>' — short share-video renders on the phone with the pipeline's bench
// switches (window.__plairShare.set): seconds (length of the test video), keepVisuals (the live scene keeps running),
// noEncode (render only), noRender (encode one still picture), encoder ({hardwareAcceleration, bitrateMode, ...}),
// keyFrameEvery. Nothing is uploaded while a bench option is set. Prints speed, bitrate and ms per frame.
import { page, sleep, openPanel, closeDialogs } from './cdp.mjs'

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
const compareSample = frame => p.evaluate(`(async () => {
  const sample = window.__plairShare.last()?.sample
  if (!sample) return ''
  const video = document.querySelector('video[src^="blob:"]')
  if (!video) return 'no finished video on the page'
  if (video.readyState < 1) await new Promise(r => video.addEventListener('loadedmetadata', r, { once: true }))
  video.currentTime = (${frame} + 0.5) / 30
  await new Promise(r => video.addEventListener('seeked', r, { once: true }))
  await new Promise(r => setTimeout(r, 300))
  const canvas = document.createElement('canvas')
  const width = canvas.width = video.videoWidth
  const height = canvas.height = video.videoHeight
  if (sample.length !== width * height * 4) return 'the sample is not the video size'
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(video, 0, 0)
  const data = ctx.getImageData(0, 0, width, height).data
  const bias = [0, 0, 0]
  let squared = 0
  for (let y = 0; y < height; y++) {
    const source = (height - 1 - y) * width * 4
    const shown = y * width * 4
    for (let x = 0; x < width; x++) {
      for (let c = 0; c < 3; c++) {
        const d = data[shown + x * 4 + c] - sample[source + x * 4 + c]
        bias[c] += d
        squared += d * d
      }
    }
  }
  const pixels = width * height
  return JSON.stringify({ psnr_dB: +(10 * Math.log10(255 * 255 / (squared / (pixels * 3)))).toFixed(2), videoMinusSource_rgb: bias.map(v => +(v / pixels).toFixed(2)) })
})()`)
try {
  await closeDialogs(p)
  await sleep(800)
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
      await closeDialogs(p)
    }
    const result = await until(`(() => {
      if (document.body.innerText.includes('Generation Failed')) return 'FAILED'
      const last = window.__plairShare.last()
      return last && last !== window.__shareBefore ? JSON.stringify({ ...last, sample: !!last.sample }) : ''
    })()`, 240, `the render (${label})`)
    const stats = result === 'FAILED' ? null : JSON.parse(result)
    if (stats?.sample) {
      console.log(label.padEnd(34), JSON.stringify(stats))
      await until(`!!document.querySelector('video[src^="blob:"]')`, 30, 'the finished video')
      console.log(''.padEnd(34), 'colour check:', await compareSample(options.sampleFrame))
    } else {
      console.log(label.padEnd(34), result)
    }
    await sleep(1500)
    await p.evaluate(`window.__plairShare.clear(), 1`)
    await closeDialogs(p)
    await sleep(1500)
  }
} finally {
  await p.evaluate(`window.__plairShare.set(null), window.__plairShare.clear(), 1`)
}
p.close()
process.exit(0)
