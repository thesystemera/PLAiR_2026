@echo off
set "SCRIPT_DIR=%~dp0"
set "PYTHON_BIN=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_BIN%" (
    echo ERROR: tts_server\.venv not found. See tts_server\README.md for setup.
    pause
    exit /b 1
)

echo ========================================
echo    PLAiR Orpheus TTS server
echo ========================================
cd /d "%SCRIPT_DIR%"
"%PYTHON_BIN%" server.py
