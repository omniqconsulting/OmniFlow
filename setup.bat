@echo off
echo ============================================
echo  FactoryOS - Environment Setup
echo ============================================

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.11+ from https://python.org
    echo        Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo Python found:
python --version

REM Remove old broken venv
if exist venv (
    echo Removing old virtual environment...
    rmdir /s /q venv
)

echo Creating new virtual environment...
python -m venv venv

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ============================================
echo  Setup complete! Run the app with:
echo     venv\Scripts\activate.bat
echo     python run.py
echo ============================================
pause
