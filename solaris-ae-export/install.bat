@echo off
REM install.bat -- Windows installer for gegenschuss_ae_export.
REM
REM Locates Houdini's hython.exe, runs install_hda.py, and prints the
REM HOUDINI_OTLSCAN_PATH line you should add to houdini.env.
REM
REM Override hython detection by setting HYTHON before running:
REM     set HYTHON=C:\Path\To\hython.exe
REM     install.bat

setlocal enabledelayedexpansion

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
set "INSTALL_PY=%HERE%\install_hda.py"
set "DEFAULT_OUT_DIR=%HERE%\otls"
set "DEFAULT_LABEL=inside repo"
set "SECRETS_AUTHORIZED=0"

if not exist "%INSTALL_PY%" (
    echo install_hda.py not found next to this script ^(%HERE%^).>&2
    exit /b 1
)

REM Optional install_secrets override.  First non-comment, non-blank
REM line is a local default path.  Gitignored, never published.
if exist "%HERE%\install_secrets" (
    for /f "usebackq tokens=* eol=#" %%i in ("%HERE%\install_secrets") do (
        if not defined _SECRET_PATH set "_SECRET_PATH=%%i"
    )
    if defined _SECRET_PATH (
        if "!_SECRET_PATH:~-1!"=="\" set "_SECRET_PATH=!_SECRET_PATH:~0,-1!"
        set "DEFAULT_OUT_DIR=!_SECRET_PATH!"
        set "DEFAULT_LABEL=from install_secrets"
        set "SECRETS_AUTHORIZED=1"
    )
)
set "DEFAULT_OUT_HDA=%DEFAULT_OUT_DIR%\gegenschuss_ae_export.hda"

REM ----- Choose install location -----
echo Where should the HDA install?
echo.
echo   [1] %DEFAULT_OUT_HDA%   (default, %DEFAULT_LABEL%)
echo   [2] Custom path
echo.
set "CHOICE="
set /p "CHOICE=Choice [1]: "
if "%CHOICE%"=="" set "CHOICE=1"

if "%CHOICE%"=="1" (
    set "OUT_HDA=%DEFAULT_OUT_HDA%"
    goto :path_chosen
)
if "%CHOICE%"=="2" (
    set "CUSTOM="
    set /p "CUSTOM=Path (file or directory): "
    if "!CUSTOM!"=="" (
        echo Empty path; cancelled.>&2
        exit /b 1
    )
    REM If it ends in .hda treat as full file path; else append filename.
    set "_TAIL=!CUSTOM:~-4!"
    if /I "!_TAIL!"==".hda" (
        set "OUT_HDA=!CUSTOM!"
    ) else (
        if "!CUSTOM:~-1!"=="\" set "CUSTOM=!CUSTOM:~0,-1!"
        set "OUT_HDA=!CUSTOM!\gegenschuss_ae_export.hda"
    )
    goto :path_chosen
)
echo Invalid choice.>&2
exit /b 1

:path_chosen
REM Confirm if path is outside the repo.  Skipped when the user took
REM the install_secrets default (CHOICE=1 with SECRETS_AUTHORIZED=1).
echo "%OUT_HDA%" | findstr /B /L /C:"%HERE%" >nul
if %ERRORLEVEL% NEQ 0 (
    if "%SECRETS_AUTHORIZED%"=="1" if "%CHOICE%"=="1" goto :outside_ok
    echo.
    echo This will create a file OUTSIDE the repo:
    echo   %OUT_HDA%
    set "YN="
    set /p "YN=Proceed? [y/N]: "
    if /I not "!YN!"=="y" if /I not "!YN!"=="yes" (
        echo Cancelled.
        exit /b 0
    )
    :outside_ok
)

for %%I in ("%OUT_HDA%") do set "OUT_DIR=%%~dpI"
if "%OUT_DIR:~-1%"=="\" set "OUT_DIR=%OUT_DIR:~0,-1%"

REM ----- Replace-existing check -----
if exist "%OUT_HDA%" (
    echo.
    echo File already exists:
    echo   %OUT_HDA%
    set "YN=y"
    set /p "YN=Replace? [Y/n]: "
    if /I "!YN!"=="n" (echo Cancelled. & exit /b 0)
    if /I "!YN!"=="no" (echo Cancelled. & exit /b 0)
)

REM 1. HYTHON env var wins.
if defined HYTHON (
    if exist "%HYTHON%" (
        set "HYTHON_BIN=%HYTHON%"
        goto :found
    )
)

REM 2. PATH lookup.
where hython.exe >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    for /f "delims=" %%i in ('where hython.exe') do (
        set "HYTHON_BIN=%%i"
        goto :found
    )
)

REM 3. Common SideFX install locations -- newest version wins.
set "HYTHON_BIN="
for /d %%d in ("C:\Program Files\Side Effects Software\Houdini *") do (
    if exist "%%d\bin\hython.exe" set "HYTHON_BIN=%%d\bin\hython.exe"
)
if defined HYTHON_BIN goto :found

echo Could not find hython.exe.>&2
echo Set HYTHON to your hython.exe path and re-run, e.g.:>&2
echo   set HYTHON=C:\Program Files\Side Effects Software\Houdini 21.0.671\bin\hython.exe>&2
echo   install.bat>&2
exit /b 1

:found
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

echo.
echo hython:    %HYTHON_BIN%
echo script:    %INSTALL_PY%
echo output:    %OUT_HDA%
echo.

"%HYTHON_BIN%" "%INSTALL_PY%" "%OUT_HDA%"
if !ERRORLEVEL! NEQ 0 exit /b !ERRORLEVEL!
endlocal
