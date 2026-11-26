@echo off
setlocal
title Kairokee
cd /d "%~dp0"
if not exist "%~dp0setup\launch.ps1" (
  echo.
  echo  Please extract the whole ZIP file first:
  echo  right-click the ZIP, choose Extract All, then open Kairokee.bat from the new folder.
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\launch.ps1" %*
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
endlocal
