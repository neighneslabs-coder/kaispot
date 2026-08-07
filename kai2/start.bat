@echo off
title KAISPOT Voucher Management
cd /d "%~dp0"
echo.
echo   Starting KAISPOT...
echo.
where python >nul 2>&1
if errorlevel 1 (
    echo   Python is not installed, or not on the PATH.
    echo.
    echo   Install Python 3.11 or newer from python.org and tick
    echo   "Add python.exe to PATH" during setup, then run this again.
    echo.
    pause
    exit /b 1
)
if not exist "data\installed.flag" (
    echo   First run - installing the libraries KAISPOT needs...
    echo.
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   Could not install the libraries. Check this PC's internet
        echo   connection and run start.bat again.
        echo.
        pause
        exit /b 1
    )
    if not exist "data" mkdir data
    echo installed > "data\installed.flag"
    echo   Done.
    echo.
)
python run.py
pause
