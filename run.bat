@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_CMD=py"

where py >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python launcher ^(py^) or python.exe was not found in PATH.
        exit /b 1
    )
    set "PYTHON_CMD=python"
)

if not "%~1"=="" goto run
%PYTHON_CMD% app.py start
goto end

:run
%PYTHON_CMD% app.py start %*

:end
endlocal
