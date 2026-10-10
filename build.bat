@echo off
setlocal
rem Builds dist\BenchBuddy\BenchBuddy.exe with the exact, tested versions (constraints-release.txt).
if not exist .venv\Scripts\python.exe (
    echo Run setup.bat first.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt -c constraints-release.txt
if errorlevel 1 (
    echo Couldn't install the pinned build dependencies.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
if errorlevel 1 (
    echo Tests failed: not building.
    pause
    exit /b 1
)
.venv\Scripts\python.exe tools\make_icon.py
.venv\Scripts\python.exe -m PyInstaller benchbuddy.spec --noconfirm --clean
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
echo.
echo Built: dist\BenchBuddy\BenchBuddy.exe
echo Photo OCR also needs the Tesseract program installed (UB-Mannheim installer, default folder is fine).
pause
