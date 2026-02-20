@echo off
setlocal enableextensions

set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

set "PY_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
if not exist "%PY_EXE%" (
  echo [ERROR] Missing venv Python: "%PY_EXE%"
  echo [ERROR] Create venv first: python -m venv .venv
  exit /b 1
)

echo [ROONIE] Repo root: %REPO_ROOT%
echo [ROONIE] Python: %PY_EXE%
echo [ROONIE] Launching Control Room for LAN monitoring...
echo [ROONIE] Bind: 0.0.0.0:8787
echo.

"%PY_EXE%" -m roonie.run_control_room --host 0.0.0.0 --port 8787 --start-live-chat --live-account bot %*
set "ERR=%ERRORLEVEL%"

if not "%ERR%"=="0" (
  echo.
  echo [ERROR] Control Room exited with code %ERR%.
)

exit /b %ERR%

