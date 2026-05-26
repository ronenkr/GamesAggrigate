@echo off
setlocal

pushd "%~dp0"

set "OUTPUT_DIR=%~1"
if not defined OUTPUT_DIR set "OUTPUT_DIR=dist\game-library"

py -3 -m game_launcher_scraper --output "%OUTPUT_DIR%"
set "EXIT_CODE=%ERRORLEVEL%"

popd
exit /b %EXIT_CODE%
