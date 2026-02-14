@echo off
setlocal
set ROOT=%~dp0
if "%~1"=="" (
  echo Usage: ops.bat ^<status^|up^|smoke^|down^> [options]
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\ops.ps1" %*
exit /b %ERRORLEVEL%
