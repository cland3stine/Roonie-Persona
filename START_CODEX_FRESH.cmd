@echo off
setlocal enableextensions

set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

set "VAULT_ROOT=D:\OBSIDIAN\AI Projects\ROONIE"
set "STATE_FILE=%VAULT_ROOT%\CURRENT_STATE.md"

if not exist "%STATE_FILE%" (
  echo [ERROR] Missing continuity file: "%STATE_FILE%"
  echo [ERROR] Verify the Obsidian ROONIE vault path and try again.
  exit /b 1
)

for /f "delims=" %%A in ('git -C "%REPO_ROOT%" rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%A"
if not defined BRANCH set "BRANCH=UNKNOWN"

for /f "delims=" %%A in ('git -C "%REPO_ROOT%" rev-parse HEAD 2^>nul') do set "COMMIT=%%A"
if not defined COMMIT set "COMMIT=UNKNOWN"

echo [ROONIE] Repo root: %REPO_ROOT%
echo [ROONIE] Branch: %BRANCH%
echo [ROONIE] Commit: %COMMIT%
echo [ROONIE] Launching Codex with Fresh Start continuity prompt...
echo.

set "PROMPT=Fresh start for ROONIE continuity. 1) Read D:\OBSIDIAN\AI Projects\ROONIE\roonie.md and CURRENT_STATE.md first. 2) Validate repo fingerprint by running git -C D:\ROONIE rev-parse --abbrev-ref HEAD and git -C D:\ROONIE rev-parse HEAD. 3) Read latest entries in D:\OBSIDIAN\AI Projects\ROONIE\10_LOGS_AND_DECISIONS\SESSION_LOG.md, DECISIONS.md, and RISKS_AND_TECH_DEBT.md. 4) Return a concise summary: NOW, NEXT, BLOCKED, TOP RISKS, last session outcomes, first recommended action. Do not make code or file changes until approved."

cmd /c codex --cd "%REPO_ROOT%" "%PROMPT%"
set "ERR=%ERRORLEVEL%"

if not "%ERR%"=="0" (
  echo.
  echo [ERROR] Codex exited with code %ERR%.
)

exit /b %ERR%
