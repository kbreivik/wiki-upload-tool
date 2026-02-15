@echo off
setlocal enabledelayedexpansion
REM Build standalone exe with Nuitka (run from project root)
REM Usage:
REM   build_nuitka.bat           — standalone folder build (recommended, best AV compat)
REM   build_nuitka.bat onefile   — single exe (may trigger AV false positives)

call %~dp0..\.venv\Scripts\activate.bat

set COMMON_OPTS=--standalone --msvc=latest --enable-plugin=pyside6 --include-data-dir=src/resources=resources --output-filename=wiki-upload-tool.exe --windows-console-mode=attach --windows-icon-from-ico=src/resources/app-icon.ico --company-name="FAJK" --product-name="Wiki Upload Tool" --file-version=1.0.0.0 --product-version=1.0.0.0 --file-description="Wiki.js bulk page upload and management tool" --copyright="Copyright 2026 FAJK"

if "%1"=="onefile" goto :build_onefile
goto :build_standalone

:build_onefile
echo.
echo WARNING: Onefile mode may trigger antivirus false positives.
echo          Use standalone mode (default) for best AV compatibility.
echo.
echo Building onefile...
python -m nuitka %COMMON_OPTS% --onefile --output-dir=dist\onefile src/main.py
if !ERRORLEVEL! neq 0 goto :buildfailed
copy README-large.md dist\onefile\README.md >nul 2>&1
set "EXE_PATH=dist\onefile\wiki-upload-tool.exe"
echo.
echo Build complete: dist\onefile\wiki-upload-tool.exe
echo NOTE: Onefile self-extracts to temp dir at runtime.
echo       This triggers heuristic AV detectors. Prefer standalone mode.
goto :sign

:build_standalone
echo Building standalone folder...
python -m nuitka %COMMON_OPTS% --no-deployment-flag=self-execution --output-dir=dist\standalone src/main.py
if !ERRORLEVEL! neq 0 goto :buildfailed
copy README-large.md dist\standalone\main.dist\README.md >nul 2>&1
set "EXE_PATH=dist\standalone\main.dist\wiki-upload-tool.exe"
echo.
echo Build complete: dist\standalone\main.dist\
echo Distribute the entire folder. Run wiki-upload-tool.exe inside it.
goto :sign

:sign
echo.
REM Optional code signing (skip if no cert available)
where signtool >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo signtool not found. Skipping code signing.
    goto :done
)
echo Signing executable...
signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "!EXE_PATH!"
if !ERRORLEVEL! equ 0 (
    echo Signing successful.
) else (
    echo Signing failed or no certificate found. Skipping.
)

:done
echo.
echo ===================================================
echo  Build complete!
echo  Output: !EXE_PATH!
echo.
echo  Recommended before distribution:
echo    1. Test with VirusTotal: https://www.virustotal.com/
echo    2. Submit to Defender:   https://www.microsoft.com/en-us/wdsi/filesubmission
echo    3. For Fortinet envs, add hash exemption on FortiGate
echo ===================================================
echo.
endlocal
goto :eof

:buildfailed
echo.
echo BUILD FAILED. Check errors above.
endlocal
exit /b 1
