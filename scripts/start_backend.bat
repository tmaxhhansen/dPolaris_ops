@echo off
setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m opsctl start-backend
  exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 -m opsctl start-backend
  exit /b %ERRORLEVEL%
)

python -m opsctl start-backend
exit /b %ERRORLEVEL%

