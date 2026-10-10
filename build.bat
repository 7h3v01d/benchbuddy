@echo off
setlocal
rem Builds, self-tests and packages BenchBuddy with the exact, tested versions (constraints-release.txt).
rem Output in dist\: BenchBuddy\BenchBuddy.exe, a release zip, SHA256SUMS and RELEASE notes.
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
echo Self-testing the built exe...
if exist dist\selftest-report.txt del dist\selftest-report.txt
start "" /wait dist\BenchBuddy\BenchBuddy.exe --self-test dist\selftest-report.txt
if errorlevel 1 (
    echo The BUILT APP failed its self-test:
    type dist\selftest-report.txt
    pause
    exit /b 1
)
type dist\selftest-report.txt
.venv\Scripts\python.exe tools\release_manifest.py dist\BenchBuddy --self-test-report dist\selftest-report.txt
if errorlevel 1 (
    echo Couldn't write the release manifest.
    pause
    exit /b 1
)
echo.
echo Built and self-tested: dist\BenchBuddy\BenchBuddy.exe
echo Release files: dist\BenchBuddy-*-win64.zip, -SHA256SUMS.txt, -RELEASE.txt
echo Photo OCR also needs the Tesseract program installed (UB-Mannheim installer, default folder is fine).
pause
