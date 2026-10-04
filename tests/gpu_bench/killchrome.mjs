import { execFileSync } from 'node:child_process'
import { basename } from 'node:path'

// Chrome's browser process detaches from the launcher on Windows, so kill every chrome.exe whose command line
// names this run's temporary profile directory (never the owner's own Chrome).
export function killProfileChrome(profileDir) {
  const name = basename(profileDir)
  const script = `Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -like '*${name}*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }`
  try { execFileSync('powershell', ['-NoProfile', '-Command', script], { stdio: 'ignore' }) } catch { /* nothing left */ }
}
