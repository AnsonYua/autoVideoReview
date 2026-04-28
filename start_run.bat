@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
cd /d "%ROOT_DIR%"

if exist "%ROOT_DIR%\.venv\Scripts\python.exe" (
  set "PYTHON_CMD=%ROOT_DIR%\.venv\Scripts\python.exe"
) else (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
  ) else (
    where py >nul 2>nul
    if not errorlevel 1 (
      set "PYTHON_CMD=py"
    ) else (
      echo Python not found. Install python or py.
      exit /b 1
    )
  )
)

if exist "%ROOT_DIR%\.env.local" call :load_env "%ROOT_DIR%\.env.local"

if not defined TELEGRAM_BOT_TOKEN set "TELEGRAM_BOT_TOKEN=8531053205:AAGuLjFSrfWgAqwrxDzMoGP1YEf_Z5OkuFs"
if not defined TELEGRAM_CHAT_ID set "TELEGRAM_CHAT_ID=8682734076"
if not defined CODEX_COMMAND set "CODEX_COMMAND=codex"
if not defined COMFYUI_BASE_URL set "COMFYUI_BASE_URL=http://127.0.0.1:8188"
if not defined COMFYUI_WORKFLOW_PATH set "COMFYUI_WORKFLOW_PATH=%ROOT_DIR%\workflow.json"
if not defined WORKFLOW_STORAGE_PATH set "WORKFLOW_STORAGE_PATH=%ROOT_DIR%\data\projects.json"
if not defined PROJECT_ID set "PROJECT_ID=project_c022d8962864"

set "PYTHONPATH=%ROOT_DIR%\src"
set "BOT_PID_FILE=%ROOT_DIR%\.telegram-bot.pid"

if exist "%BOT_PID_FILE%" (
  for /f "usebackq delims=" %%P in ("%BOT_PID_FILE%") do set "OLD_BOT_PID=%%P"
  if defined OLD_BOT_PID (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$pidValue = '!OLD_BOT_PID!'; $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue; if ($proc) { Write-Host ('Stopping previous bot process PID ' + $pidValue); Stop-Process -Id $pidValue -Force }"
  )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = $env:ROOT_DIR; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*grok_workflow.cli*' -and $_.CommandLine -like ('*' + $root + '*') } | ForEach-Object { Write-Host ('Stopping stale bot process PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:PYTHONPATH = '%PYTHONPATH%'; $process = Start-Process -FilePath '%PYTHON_CMD%' -ArgumentList @('-m','grok_workflow.cli') -WorkingDirectory '%ROOT_DIR%' -NoNewWindow -PassThru; Set-Content -LiteralPath '%BOT_PID_FILE%' -Value $process.Id; Wait-Process -Id $process.Id; exit $process.ExitCode"
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo start_run.bat failed with exit code %EXIT_CODE%.
  pause
)

exit /b %EXIT_CODE%

:load_env
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~1") do (
  if not "%%A"=="" (
    set "ENV_VALUE=%%~B"
    call set "%%A=%%ENV_VALUE%%"
  )
)
exit /b 0
