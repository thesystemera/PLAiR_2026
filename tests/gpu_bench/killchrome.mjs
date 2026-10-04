import { execFileSync } from 'node:child_process'
import { basename } from 'node:path'

// Chrome's browser process detaches from the launcher on Windows, so kill every chrome.exe whose command line
// names this run's temporary profile directory (never the owner's own Chrome), and repeat until none is left.
export function killProfileChrome(profileDir) {
  const name = basename(profileDir)
  const script = `for ($i = 0; $i -lt 5; $i++) {
    $left = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -like '*${name}*' }
    if (-not $left) { break }
    $left | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
  }`
  try { execFileSync('powershell', ['-NoProfile', '-Command', script], { stdio: 'ignore' }) } catch { /* nothing left */ }
}
