@echo off
REM Double-click this file to deploy code to the server
REM (The real logic lives in deploy.ps1)

chcp 65001 > nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   Dota2 inhouse stats - deploy code to server
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*

echo.
echo ============================================================
echo   Done. Press any key to close...
echo ============================================================
pause > nul
