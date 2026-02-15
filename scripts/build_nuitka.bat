@echo off
REM Build standalone exe with Nuitka (run from project root)
REM Usage:
REM   build_nuitka.bat           — builds onefile (single exe, may trigger AV)
REM   build_nuitka.bat folder    — builds standalone folder (less AV issues)

call %~dp0..\.venv\Scripts\activate.bat

set COMMON_OPTS=--standalone ^
    --enable-plugin=pyside6 ^
    --include-data-dir=src/resources=resources ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=src/resources/app-icon.ico ^
    --company-name="wiki-upload-tool" ^
    --product-name="Wiki.js Upload Tool" ^
    --file-version=0.1.0 ^
    --product-version=0.1.0

if "%1"=="folder" (
    echo Building standalone folder...
    python -m nuitka %COMMON_OPTS% ^
        --output-dir=dist\folder ^
        --output-filename=wiki-upload-tool.exe ^
        src/main.py
    copy README-large.md dist\folder\main.dist\README.md >nul 2>&1
    echo.
    echo Build complete: dist\folder\main.dist\
    echo Distribute the entire folder. Run wiki-upload-tool.exe inside it.
) else (
    echo Building onefile...
    python -m nuitka %COMMON_OPTS% ^
        --onefile ^
        --output-dir=dist ^
        --output-filename=wiki-upload-tool.exe ^
        src/main.py
    copy README-large.md dist\README.md >nul 2>&1
    echo.
    echo Build complete: dist\wiki-upload-tool.exe
    echo NOTE: May trigger AV. See README for exclusion instructions.
)

echo.
echo Done.
