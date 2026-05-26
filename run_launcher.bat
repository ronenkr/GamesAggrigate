@echo off
setlocal

cd /d "%~dp0"

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js with npm and try again.
  exit /b 1
)

if not exist "node_modules\electron" (
  echo [INFO] Dependencies not found. Running npm install...
  call npm install
  if errorlevel 1 (
    echo [ERROR] npm install failed.
    exit /b 1
  )
)

echo [INFO] Starting Games Librarian...
call npm run start
exit /b %errorlevel%
