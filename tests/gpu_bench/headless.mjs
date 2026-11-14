// node headless.mjs <url> [seconds] — headless Chrome on the P6000 (LUID=high,low from list_gpus.py) left open on a
// page so the phone scripts in perf/ can drive it: run them with CDP_PORT=<PORT> PAGE=<part of the url>. WINDOW=412,915
// (default) gives the phone layout. Closes by itself after [seconds] (default 600) or when this process is stopped.
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { killProfileChrome } from './killchrome.mjs'

const [url = 'http://127.0.0.1:4103', seconds = '600'] = process.argv.slice(2)
const PORT = Number(process.env.PORT || 9338)
const sleep = ms => new Promise(r => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), 'plair-headless-'))
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--use-angle=d3d11', `--use-adapter-luid=${process.env.LUID || '0,9922884'}`,
  `--window-size=${process.env.WINDOW || '412,915'}`, '--autoplay-policy=no-user-gesture-required',
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', url,
], { stdio: 'ignore' })
let closing = false
const close = async () => {
  if (closing) return
  closing = true
  killProfileChrome(profile)
  await sleep(500)
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 }) } catch { /* still locked */ }
  process.exit(0)
}
process.on('SIGINT', close)
process.on('SIGTERM', close)
let target = null
for (let i = 0; i < 50 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(p => p.type === 'page') } catch { /* starting */ }
  if (!target) await sleep(200)
}
console.log(target ? `ready on port ${PORT}: ${target.url}` : 'Chrome did not start')
await sleep(Number(seconds) * 1000)
await close()
