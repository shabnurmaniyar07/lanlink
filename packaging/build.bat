@echo off
rem Build LanLink.exe and the installer. Run from the repository root:
rem
rem     packaging\build.bat
rem
rem Needs: Python 3.11+, and Inno Setup 6 for the installer step
rem (https://jrsoftware.org/isdl.php). Without Inno Setup the .exe is still
rem built and the script says so.

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo.
echo === LanLink build =================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv
    py -3 -m venv .venv || goto :failed
)
set PY=.venv\Scripts\python.exe

echo Installing LanLink and the build tools
"%PY%" -m pip install --upgrade pip --quiet || goto :failed
"%PY%" -m pip install -e ".[dev]" --quiet || goto :failed
"%PY%" -m pip install pyinstaller --quiet || goto :failed

echo Reading the version
for /f "usebackq delims=" %%v in (`%PY% tools\print_version.py`) do set VERSION=%%v
if "%VERSION%"=="" goto :failed
echo   version %VERSION%

echo Writing the Windows version resource
"%PY%" tools\sync_version.py || goto :failed

echo Running the tests
"%PY%" -m pytest -q || goto :failed

echo Building LanLink.exe
if exist "build\LanLink" rmdir /s /q "build\LanLink"
if exist "dist\LanLink" rmdir /s /q "dist\LanLink"
"%PY%" -m PyInstaller packaging\lanlink.spec --noconfirm --clean || goto :failed

if not exist "dist\LanLink\LanLink.exe" (
    echo.
    echo BUILD FAILED: dist\LanLink\LanLink.exe was not produced.
    goto :failed
)
echo   built dist\LanLink\LanLink.exe

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe

if "%ISCC%"=="" (
    echo.
    echo Inno Setup 6 was not found, so no installer was built.
    echo The application itself is ready in dist\LanLink\
    echo Install Inno Setup from https://jrsoftware.org/isdl.php and run this again
    echo if you want LanLinkSetup-%VERSION%.exe as well.
    goto :done
)

echo Building the installer
"%ISCC%" /DAppVersion=%VERSION% packaging\lanlink.iss || goto :failed
echo   built packaging\output\LanLinkSetup-%VERSION%.exe

echo Packing the portable zip
powershell -NoProfile -Command "Compress-Archive -Force -Path dist\LanLink\* -DestinationPath packaging\output\LanLink-%VERSION%-portable.zip" || goto :failed

echo Writing SHA256SUMS.txt
powershell -NoProfile -Command "Push-Location packaging\output; Get-ChildItem -File ^| Where-Object { $_.Name -ne 'SHA256SUMS.txt' } ^| ForEach-Object { $h=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(); \"$h  $($_.Name)\" } ^| Set-Content -Encoding ascii SHA256SUMS.txt; Pop-Location" || goto :failed
echo   built packaging\output\SHA256SUMS.txt

:done
echo.
echo === Finished ======================================================
echo   Application:  dist\LanLink\LanLink.exe
if not "%ISCC%"=="" echo   Installer:    packaging\output\LanLinkSetup-%VERSION%.exe
if not "%ISCC%"=="" echo   Portable:     packaging\output\LanLink-%VERSION%-portable.zip
if not "%ISCC%"=="" echo   Checksums:    packaging\output\SHA256SUMS.txt
echo.
echo Attach all three to the GitHub release. Without SHA256SUMS.txt no LanLink
echo will install the update, because it cannot verify what it downloaded.
echo.
echo Publish the installer as a GitHub release tagged v%VERSION% and every
echo LanLink with that repository configured will notice the new version.
echo.
endlocal
exit /b 0

:failed
echo.
echo === Build failed ==================================================
echo Nothing above this line succeeded past the last step it printed.
endlocal
exit /b 1
