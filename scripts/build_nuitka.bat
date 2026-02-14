@echo off
REM Build standalone exe with Nuitka (run from project root)
REM Requires: MSVC (Visual Studio Build Tools), Nuitka, PySide6
REM Install: uv pip install nuitka

call %~dp0..\.venv\Scripts\activate.bat

python -m nuitka ^
    --standalone ^
    --onefile ^
    --enable-plugin=pyside6 ^
    --include-data-dir=src/resources=resources ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=src/resources/app-icon.ico ^
    --company-name="wiki-upload-tool" ^
    --product-name="Wiki.js Upload Tool" ^
    --file-version=0.1.0 ^
    --product-version=0.1.0 ^
    --output-dir=dist ^
    --output-filename=wiki-upload-tool.exe ^
    src/main.py
