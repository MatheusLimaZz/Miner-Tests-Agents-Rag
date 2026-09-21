@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%src;%PYTHONPATH%"
if "%~1"=="" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -m msrkit.cli menu
) else (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -m msrkit.cli %*
)
