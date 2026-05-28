@echo off
REM Double-click this file to run sync.ps1
REM (The real logic lives in sync.ps1)

chcp 65001 > nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   Dota2 inhouse stats - fetch locally and sync to server
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync.ps1" %*

echo.
echo ============================================================
echo   Done. Press any key to close...
echo ============================================================
pause > nul
