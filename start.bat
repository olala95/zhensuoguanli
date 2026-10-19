@echo off
rem ============================================================
rem  Clinic System launcher
rem  Double-click to start. Extra args are passed to main.py, e.g.
rem      start.bat --port 8790 --no-browser
rem
rem  Requires Python 3.9+ . Python 3.8 is REFUSED on purpose:
rem  its socket.getfqdn() crashes on Windows with a non-ASCII
rem  computer name (UnicodeDecodeError), and http.server always
rem  calls it when binding the port.
rem ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "PYEXE="

rem ---- 1) project-local venv ----
if exist "%~dp0.venv\Scripts\python.exe" call :check "%~dp0.venv\Scripts\python.exe"

rem ---- 2) WorkBuddy managed Python 3.13 ----
if not defined PYEXE (
  for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\3.13*") do (
    if not defined PYEXE if exist "%%D\python.exe" call :check "%%D\python.exe"
  )
)

rem ---- 3) common install locations ----
if not defined PYEXE (
  for %%V in (313 312 311 310 39) do (
    if not defined PYEXE (
      if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        call :check "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
      )
    )
  )
)

rem ---- 4) py launcher (resolve to a real path first) ----
if not defined PYEXE (
  where py >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
      if not defined PYEXE call :check "%%P"
    )
  )
)

if not defined PYEXE (
  echo.
  echo   [ERROR] No usable Python found.
  echo.
  echo   This program needs Python 3.9 or newer.
  echo   Python 3.8 does NOT work: on Windows machines with a
  echo   non-ASCII computer name its socket.getfqdn crashes,
  echo   which breaks the built-in web server.
  echo.
  echo   Install from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

echo.
echo   Using Python: %PYEXE%
echo   Starting clinic system...
echo.

"%PYEXE%" "%~dp0src\main.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo   [EXIT] code %RC%
  echo   Check logs\error.log for details.
  echo.
  pause
)
endlocal & exit /b %RC%

rem ------------------------------------------------------------ 
:check
rem Accept the candidate only if it is Python 3.9 or newer.
"%~1" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if not errorlevel 1 set "PYEXE=%~1"
exit /b 0
