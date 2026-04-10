@echo off
setlocal

if defined CCOLLAB_RUNTIME_ROOT (
    set "ROOT=%CCOLLAB_RUNTIME_ROOT%"
) else (
    for %%I in ("%~dp0..") do set "ROOT=%%~fI"
)

set "PYTHON_LAUNCHER="
call :try_python py -3
if not defined PYTHON_LAUNCHER call :try_python python
if not defined PYTHON_LAUNCHER call :try_python python3

if not defined PYTHON_LAUNCHER (
    >&2 echo Unable to find Python 3.9+. Install Python 3.9 or newer and ensure py, python, or python3 is on PATH.
    exit /b 1
)

if defined PYTHONPATH (
    set "PYTHONPATH=%ROOT%;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%ROOT%"
)

call %PYTHON_LAUNCHER% -m runtime.cli %*
exit /b %errorlevel%

:try_python
where %1 >nul 2>nul
if errorlevel 1 goto :eof
if "%~2"=="" (
    %1 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
    if errorlevel 1 goto :eof
    set "PYTHON_LAUNCHER=%1"
    goto :eof
)
%1 %2 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto :eof
set "PYTHON_LAUNCHER=%1 %2"
goto :eof
