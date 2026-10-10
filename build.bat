@echo off
setlocal
rem Builds dist\BenchBuddy\BenchBuddy.exe (run setup.bat once first)
if not exist .venv\Scripts\python.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pip install -q "pyinstaller>=6" pillow
.venv\Scripts\python.exe tools\make_icon.py
.venv\Scripts\python.exe -m PyInstaller benchbuddy.spec --noconfirm --clean
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
echo.
echo Built: dist\BenchBuddy\BenchBuddy.exe
pause
