@echo off
setlocal EnableDelayedExpansion
title PLAiR Start
set "ROOT=E:\AI_RADIO"
set "NGINX=C:\nginx"
set "PY=%ROOT%\.venv\Scripts\python.exe"

echo ========================================
echo    PLAiR START (PLAiR only - other sites untouched)
echo ========================================
echo.

echo [1/5] Stopping any running PLAiR backend...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\external_components\plair_stop.ps1"
if errorlevel 1 (
    echo.
    echo An older PLAiR backend is still open - not starting a second one.
    pause
    exit /b 1
)

echo [2/5] Building frontend...
cd /d "%ROOT%\client"
call npm run build
if errorlevel 1 (
    echo.
    echo BUILD FAILED - backend not started.
    pause
    exit /b 1
)

echo [3/5] nginx...
cd /d "%NGINX%"
tasklist /FI "IMAGENAME eq nginx.exe" | find /I "nginx.exe" >nul
if errorlevel 1 (
    nginx.exe -t && start "" nginx.exe
    echo    - nginx started.
) else (
    nginx.exe -t && nginx.exe -s reload
    if errorlevel 1 (
        echo    - nginx reload needs an Administrator window. Right-click the shortcut ^> Run as administrator
        echo      ^(only needed after nginx.conf changes; the site still works without it^).
    ) else (
        echo    - nginx reloaded gracefully.
    )
)

echo [4/5] Starting backend (also launches the TTS engine on the P6000)...
start "Plair Backend (Port 8000)" cmd /k "cd /d %ROOT%\server && %PY% start.py"

echo [5/5] Waiting for PLAiR to become healthy (model loading takes a few minutes)...
powershell -NoProfile -Command "$d=(Get-Date).AddMinutes(8); while((Get-Date) -lt $d){ try { if(((Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 2).status -eq 'ok') -and ((Invoke-RestMethod http://127.0.0.1:8090/health -TimeoutSec 2).status -eq 'ok')){ exit 0 } } catch {}; Start-Sleep 2 }; exit 1"
if errorlevel 1 (
    echo    - Backend did not become healthy within 8 minutes. Check the "Plair Backend" window.
    pause
    exit /b 1
)

echo.
"%PY%" "%ROOT%\tests\smoke_test.py" --skip-dj
echo.
echo PLAiR is live at https://plair.live  (close the "Plair Backend" window to stop it)
pause
