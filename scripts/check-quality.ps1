[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$client = Join-Path $projectRoot 'client'
$pythonTargets = @('server', 'tts_server\server.py', 'tests')
$vendored = 'server/Apollo'
$qualityFailures = [System.Collections.Generic.List[string]]::new()

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Repository Python is missing (.venv).'
}
if (-not (Test-Path -LiteralPath (Join-Path $client 'node_modules\.bin\knip.cmd') -PathType Leaf)) {
    throw 'Frontend quality tools are missing. Run: npm install --prefix client'
}

function Invoke-QualityStep {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][scriptblock]$Command
    )
    Write-Host "`n[$Label]" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        $qualityFailures.Add("$Label failed with exit code $LASTEXITCODE")
        Write-Warning $qualityFailures[$qualityFailures.Count - 1]
    }
}

Push-Location -LiteralPath $projectRoot
try {
    Invoke-QualityStep 'Python syntax' {
        & $python -m compileall -q -x 'Apollo' @pythonTargets
    }
    Invoke-QualityStep 'Python correctness lint (undefined and unused names)' {
        & $python -m ruff check --select F,E902 --exclude $vendored @pythonTargets
    }
    Invoke-QualityStep 'Python dead code' {
        & $python -m vulture server tts_server\server.py --min-confidence 100 --exclude $vendored
    }

    Push-Location -LiteralPath $client
    try {
        Invoke-QualityStep 'Frontend lint' { & npm run --silent lint }
        Invoke-QualityStep 'Frontend UI state (selectors and engine keys)' { & npm run --silent check:ui-state }
        Invoke-QualityStep 'Frontend motion (no CSS transitions on animated elements)' { & npm run --silent check:motion }
        Invoke-QualityStep 'Frontend dead code (files, exports, dependencies)' { & npm run --silent dead-code }
        if (-not $SkipBuild) {
            Invoke-QualityStep 'Frontend production build' { & npm run --silent build }
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}

if ($qualityFailures.Count) {
    Write-Host ($qualityFailures -join "`n") -ForegroundColor Red
    exit 1
}
Write-Host "`nOffline quality checks passed." -ForegroundColor Green
