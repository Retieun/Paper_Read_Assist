@echo off
REM PapAssist launcher for Windows. Usage: papassist.bat [path\to\paper.tex or folder or .zip]
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    set "PY=python"
  ) else (
    echo Python 3.11 or newer was not found.
    echo Install it from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH"^)
    echo or run:  winget install Python.Python.3.12
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment (first run only^)...
  %PY% -m venv .venv || (echo Failed to create a virtual environment.& pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || (echo Dependency installation failed.& pause & exit /b 1)
)

if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
)
if "%ANTHROPIC_API_KEY%"=="" (
  echo [PapAssist] No ANTHROPIC_API_KEY found: running without the LLM. Put ANTHROPIC_API_KEY=... in a .env file next to this script to enable it.
)

".venv\Scripts\python.exe" -m papassist %*
if errorlevel 1 pause
endlocal
