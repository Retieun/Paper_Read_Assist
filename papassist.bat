@echo off
REM PapAssist launcher for Windows. Usage: papassist.bat [path\to\paper.tex or folder or .zip]
setlocal
cd /d "%~dp0"

REM Find Python 3.11 or newer: the "py" launcher with a version first, then python on PATH.
set "PY="
for %%V in (3.13 3.12 3.11) do call :trypy py -%%V
call :trypy py -3
call :trypy python
call :trypy python3
if defined PY goto :havepy
echo PapAssist needs Python 3.11 or newer, and none was found on this computer.
echo Install it from https://www.python.org/downloads/windows/ ^(tick "Add python.exe to PATH"^)
echo or run:  winget install Python.Python.3.12
echo Then run this file again.
pause
exit /b 1

:trypy
if defined PY exit /b 0
%* -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul && set "PY=%*"
exit /b 0

:havepy
if not exist ".venv\Scripts\python.exe" goto :mkvenv
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 goto :deps
echo Recreating the Python environment ^(the existing one used an older Python^)...
rmdir /s /q .venv
:mkvenv
echo Creating the Python environment with %PY% ^(first run only^)...
%PY% -m venv .venv || goto :venvfail

:deps
REM Install the dependencies, or finish an installation that failed or was interrupted earlier.
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, pypandoc, anthropic, bibtexparser, pylatexenc, dotenv" >nul 2>nul
if not errorlevel 1 goto :run
echo Installing dependencies ^(takes a minute^)...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :depsfail
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, pypandoc, anthropic, bibtexparser, pylatexenc, dotenv" >nul 2>nul || goto :depsfail

:run
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
)
if "%ANTHROPIC_API_KEY%"=="" echo [PapAssist] No ANTHROPIC_API_KEY found: running without the LLM. Put ANTHROPIC_API_KEY=... in a .env file next to this script to enable it.
".venv\Scripts\python.exe" -m papassist %*
if errorlevel 1 pause
endlocal
exit /b 0

:venvfail
echo Failed to create a virtual environment.
pause
exit /b 1

:depsfail
echo.
echo Dependency installation failed ^(see the messages above^). Check your internet connection and run this file again.
pause
exit /b 1
