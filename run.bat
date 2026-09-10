@echo off
rem ==============================================================================
rem Compliance & Risk Agent - Windows Startup Script
rem
rem Usage:
rem   run.bat              - Start on default port 8000 with auto-reload
rem   run.bat 8080         - Start on port 8080
rem   run.bat --no-reload  - Start without auto-reload
rem
rem You can also double-click this file in Windows Explorer.
rem ==============================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

rem --- Default configuration ---
set "PORT=8000"
if defined PORT_ENV set "PORT=%PORT_ENV%"
set "RELOAD=1"
set "UVICORN_ARGS="

rem --- Parse command-line arguments ---
:parse_args
if "%~1"=="" goto after_parse
set "ARG=%~1"

rem Check if argument is a port number
echo %ARG%| findstr /r "^[0-9][0-9]*$" >nul
if not errorlevel 1 (
    set "PORT=%ARG%"
    shift
    goto parse_args
)

if "%ARG%"=="--no-reload" (
    set "RELOAD=0"
    shift
    goto parse_args
)
if "%ARG%"=="--reload" (
    set "RELOAD=1"
    shift
    goto parse_args
)

set "UVICORN_ARGS=!UVICORN_ARGS! %ARG%"
shift
goto parse_args

:after_parse
if "%RELOAD%"=="1" (
    set "UVICORN_ARGS=--reload !UVICORN_ARGS!"
)

rem --- 1. Auto-bootstrap .env if missing ------------------------------------
if not exist ".env" (
    if exist ".env.example" (
        echo [run] .env not found -- copying from .env.example
        copy ".env.example" ".env" >nul
    )
)

rem --- 2. Check for Python executable ----------------------------------------
set "PYTHON_EXE="
where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_EXE=python"
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py -3"
    )
)

if not defined PYTHON_EXE (
    if not exist ".venv\Scripts\python.exe" (
        echo [run] ERROR: Python was not found in your PATH.
        echo [run] Please install Python 3.9+ from https://www.python.org/downloads/
        echo [run] and ensure "Add Python to PATH" is checked during installation.
        pause
        exit /b 1
    )
)

rem --- 3. Auto-bootstrap virtual environment & requirements ------------------
if not exist ".venv\Scripts\python.exe" (
    echo [run] .venv not found -- creating virtual environment (.venv)...
    !PYTHON_EXE! -m venv .venv
    if errorlevel 1 (
        echo [run] ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [run] installing dependencies from requirements.txt...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [run] ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
    type nul > .venv\.requirements_installed
)

set "PY=.venv\Scripts\python.exe"

rem --- 4. Free the port if already in use -----------------------------------
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r ":%PORT% " ^| findstr "LISTENING"') do (
    echo [run] Port %PORT% is in use by PID %%a -- stopping old server...
    taskkill /F /PID %%a >nul 2>&1
)

rem --- 5. Ensure sample PDFs exist -------------------------------------------
if not exist "sample_docs\1_GDPR_Art28_DPA_Requirements.pdf" (
    echo [run] generating sample PDFs into sample_docs\...
    "%PY%" -c "import demo; demo.make_sample_pdfs()"
)

rem --- 6. Start the server ---------------------------------------------------
echo [run] starting: uvicorn app.main:app --host 0.0.0.0 --port %PORT% %UVICORN_ARGS%
echo [run] open http://localhost:%PORT%   *   health: http://localhost:%PORT%/api/health
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port %PORT% %UVICORN_ARGS%

if errorlevel 1 (
    echo [run] Server exited with an error.
    pause
)
