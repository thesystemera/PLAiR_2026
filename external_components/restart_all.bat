@echo off
SETLOCAL EnableDelayedExpansion

REM ================= CONFIGURATION =================
SET "START_DIR=%CD%"
SET NGINX_PATH=C:\nginx
SET PROJECT_ROOT=E:\AI_RADIO
SET BACKEND_PATH=%PROJECT_ROOT%\server
SET CLIENT_PATH=%PROJECT_ROOT%\client
SET PYTHON_PATH=%PROJECT_ROOT%\.venv\Scripts\python.exe
REM =================================================

echo ========================================
echo    PLAIR.LIVE PRODUCTION RELOAD (HARD)
echo ========================================
echo.

REM ---------------------------------------------
REM 1. KILL PROCESSES (THE NUCLEAR OPTION)
REM ---------------------------------------------
echo [1/5] Killing ALL services...

REM CRITICAL: Kill old backend cmd windows by title to prevent multiple instances!
echo    - Killing old backend windows...
taskkill /F /FI "WINDOWTITLE eq Plair Backend*" /T 2>nul

:KILL_NGINX
taskkill /F /IM nginx.exe /T 2>nul
if !ERRORLEVEL! EQU 0 echo    - Nginx killed.

REM Kill Python (Backend & Scripts)
taskkill /F /IM python.exe /T 2>nul
if !ERRORLEVEL! EQU 0 echo    - Python killed.

REM Kill Node (Vite/React build locks)
taskkill /F /IM node.exe /T 2>nul
if !ERRORLEVEL! EQU 0 echo    - Node.js killed.

REM Kill Java (Sometimes lurking)
taskkill /F /IM java.exe /T 2>nul

REM Kill port 8000 (Backend cleanup)
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a /T 2>nul
    echo    - Backend (Port 8000^) killed.
)

REM Kill port 3000 (Frontend cleanup)
for /f "tokens=5" %%a in ('netstat -aon ^| find ":3000" ^| find "LISTENING"') do (
    taskkill /F /PID %%a /T 2>nul
    echo    - Frontend (Port 3000^) killed.
)

timeout /t 2 /nobreak >nul
echo.

REM ---------------------------------------------
REM 2. PURGE CACHES
REM ---------------------------------------------
echo [2/5] Purging Nginx Cache...

if exist "%NGINX_PATH%\cache" (
    rd /s /q "%NGINX_PATH%\cache" >nul 2>&1
    echo    - Deleted Nginx cache folder.
    mkdir "%NGINX_PATH%\cache"
)
if exist "%NGINX_PATH%\temp" (
    rd /s /q "%NGINX_PATH%\temp" >nul 2>&1
    echo    - Deleted Nginx temp folder.
    REM FIXED: Recreate the temp folder immediately so Nginx doesn't crash
    mkdir "%NGINX_PATH%\temp"
    mkdir "%NGINX_PATH%\temp\client_body_temp"
    mkdir "%NGINX_PATH%\temp\proxy_temp"
    mkdir "%NGINX_PATH%\temp\fastcgi_temp"
    mkdir "%NGINX_PATH%\temp\uwsgi_temp"
    mkdir "%NGINX_PATH%\temp\scgi_temp"
) else (
    REM If it didn't exist at all, create it now
    mkdir "%NGINX_PATH%\temp"
    mkdir "%NGINX_PATH%\temp\client_body_temp"
)
echo.

REM ---------------------------------------------
REM 3. REBUILD FRONTEND
REM ---------------------------------------------
echo [3/5] Rebuilding Frontend (Vite)...
cd /d "%CLIENT_PATH%"

REM Clear Vite cache
echo    - Clearing Vite cache...
if exist "node_modules\.vite" (
    rd /s /q "node_modules\.vite" >nul 2>&1
    echo      - Vite cache cleared.
)

:DELETE_DIST
if exist "dist" (
    echo    - Attempting to delete dist folder...
    rd /s /q "dist" >nul 2>&1
    
    if exist "dist" (
        echo      * File lock detected. Switching to Rename Strategy...
        
        REM Generate a random number for the trash folder
        set "TRASH_NAME=dist_trash_!RANDOM!"
        
        ren dist "!TRASH_NAME!" >nul 2>&1
        
        if exist "dist" (
             echo.
             echo ****************************************
             echo CRITICAL FAILURE: FILE LOCK
             echo Could not delete OR rename the 'dist' folder.
             echo Please manually close the folder in Windows Explorer.
             echo ****************************************
             pause
             exit /b 1
        ) else (
             echo      - SUCCESS: Locked folder renamed to '!TRASH_NAME!'.
             echo        (You can delete the trash folder later manually)
        )
    )
)

echo    - Running npm build...
call npm run build

IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo ****************************************
    echo CRITICAL ERROR: BUILD FAILED
    echo The app will NOT start because the build broke.
    echo ****************************************
    pause
    exit /b %ERRORLEVEL%
)
echo    - Build Successful.
echo.

REM ---------------------------------------------
REM 4. START NGINX
REM ---------------------------------------------
echo [4/5] Starting Nginx...
cd /d "%NGINX_PATH%"

REM VALIDATE CONFIG BEFORE STARTING
echo    - Testing Nginx Configuration...
nginx.exe -t
IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo ****************************************
    echo CRITICAL ERROR: NGINX CONFIG IS BROKEN
    echo Please check the error message above.
    echo The previous config changes might have a syntax error.
    echo ****************************************
    pause
    exit /b 1
)

start nginx
timeout /t 2 /nobreak >nul
echo    - Nginx started.
echo.

REM ---------------------------------------------
REM 5. START BACKEND
REM ---------------------------------------------
echo [5/5] Starting Backend...
echo    - Target: %BACKEND_PATH%\start.py
echo    - Python: %PYTHON_PATH%

start "Plair Backend (Port 8000)" cmd /k "cd /d %BACKEND_PATH% && %PYTHON_PATH% start.py"
echo    - Backend started.

echo.
echo ========================================
echo    SYSTEM IS LIVE WITH FRESH CODE
echo ========================================
echo.
echo Window will stay open - type 'exit' to close
cd /d "%START_DIR%"
cmd /k