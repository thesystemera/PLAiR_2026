$maxDed = 0; $maxShared = 0; $seen = $false
while ($true) {
  $ps = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*process_backlog.py*' })
  if ($ps.Count -eq 0) { if ($seen) { break } else { Start-Sleep 2; continue } }
  $seen = $true; $ded = 0; $sh = 0
  foreach ($p in $ps) {
    $c = (Get-Counter "\GPU Process Memory(pid_$($p.ProcessId)_*)\Dedicated Usage","\GPU Process Memory(pid_$($p.ProcessId)_*)\Shared Usage" -ErrorAction SilentlyContinue).CounterSamples
    $ded += ($c | Where-Object { $_.Path -like '*dedicated usage' } | Measure-Object CookedValue -Sum).Sum
    $sh += ($c | Where-Object { $_.Path -like '*shared usage' } | Measure-Object CookedValue -Sum).Sum
  }
  if ($ded -gt $maxDed) { $maxDed = $ded }; if ($sh -gt $maxShared) { $maxShared = $sh }
  Start-Sleep 2
}
"backlog process peak: dedicated {0:N0} MB, spilled to system RAM {1:N0} MB" -f ($maxDed/1MB), ($maxShared/1MB)
