$ErrorActionPreference = 'SilentlyContinue'
$found = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -and (
        ($_.CommandLine -match 'AI_RADIO\\server' -and $_.CommandLine -match 'start\.py') -or
        ($_.CommandLine -match 'AI_RADIO\\\.venv\\Scripts\\python\.exe"?\s+start\.py') -or
        ($_.CommandLine -match 'AI_RADIO\\tts_chatterbox'))
})
$ports = @(Get-NetTCPConnection -LocalPort 8000, 8090 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique)
$ids = @($found | Select-Object -ExpandProperty ProcessId) + $ports | Where-Object { $_ } | Select-Object -Unique
foreach ($id in $ids) { taskkill /F /T /PID $id 2>$null | Out-Null }
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline -and (Get-NetTCPConnection -LocalPort 8000, 8090 -State Listen)) { Start-Sleep -Milliseconds 500 }
$left = @(Get-NetTCPConnection -LocalPort 8000, 8090 -State Listen).Count
if ($ids.Count -eq 0) { Write-Host '   - no PLAiR backend was running.' }
elseif ($left -eq 0) { Write-Host "   - stopped $($ids.Count) PLAiR process tree(s), windows included." }
else {
    Write-Host '   - a PLAiR backend is still running and could not be stopped (it was started as Administrator?).'
    Write-Host '     Close its window, or run PLAiR Start as administrator.'
    exit 1
}
