@echo off
setlocal

if defined CCOLLAB_RUNTIME_ROOT (
    set "ROOT=%CCOLLAB_RUNTIME_ROOT%"
) else (
    for %%I in ("%~dp0..") do set "ROOT=%%~fI"
)

set "PYTHON_LAUNCHER="
where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_LAUNCHER=py -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_LAUNCHER=python"
    ) else (
        where python3 >nul 2>nul
        if not errorlevel 1 (
            set "PYTHON_LAUNCHER=python3"
        )
    )
)

if not defined PYTHON_LAUNCHER (
    >&2 echo Unable to find Python. Install Python 3 and ensure py, python, or python3 is on PATH.
    exit /b 1
)

if defined PYTHONPATH (
    set "PYTHONPATH=%ROOT%;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%ROOT%"
)

call %PYTHON_LAUNCHER% -m runtime.cli %*
