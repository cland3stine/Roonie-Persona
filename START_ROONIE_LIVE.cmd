@echo off
setlocal enableextensions

set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

set "PY_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
set "RUNNER=%REPO_ROOT%\run_control_room.py"
if not exist "%PY_EXE%" (
  echo [ERROR] Missing venv Python: "%PY_EXE%"
  echo [ERROR] Create venv first: python -m venv .venv
  exit /b 1
)
if not exist "%RUNNER%" (
  echo [ERROR] Missing launcher: "%RUNNER%"
  exit /b 1
)

if not defined ROONIE_ENFORCE_SETUP_GATE set "ROONIE_ENFORCE_SETUP_GATE=1"

set "AUDIO_FLAG="
if defined ROONIE_AUDIO_ENABLED (
  if "%ROONIE_AUDIO_ENABLED%"=="1" set "AUDIO_FLAG=--start-audio"
)

echo [ROONIE] Repo root: %REPO_ROOT%
echo [ROONIE] Python: %PY_EXE%
echo [ROONIE] Launching Control Room for LAN monitoring...
echo [ROONIE] Bind: 0.0.0.0:8787
echo [ROONIE] Setup gate: %ROONIE_ENFORCE_SETUP_GATE% (ROONIE_ENFORCE_SETUP_GATE)
if defined AUDIO_FLAG echo [ROONIE] Audio: ENABLED (ROONIE_AUDIO_ENABLED=1)
echo.

"%PY_EXE%" "%RUNNER%" --host 0.0.0.0 --port 8787 --start-live-chat --live-account bot %AUDIO_FLAG% %*
set "ERR=%ERRORLEVEL%"

if not "%ERR%"=="0" (
  echo.
  echo [ERROR] Control Room exited with code %ERR%.
)

exit /b %ERR%

