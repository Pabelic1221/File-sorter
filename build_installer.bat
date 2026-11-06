@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo   Folder Sorter - build the Setup installer
echo ============================================
echo.
echo [1/3] Installing build tools...
python -m pip install --upgrade pywebview pyinstaller || goto :fail
echo.
echo [2/3] Building FolderSorter.exe...
python -m PyInstaller --noconfirm --onefile --noconsole --collect-all webview --icon icon.ico --name FolderSorter foldersorter.py || goto :fail
echo.
echo [3/3] Building the installer...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if "%ISCC%"=="" goto :noinno
"%ISCC%" installer.iss || goto :fail
echo.
echo Done! Your installer is in the "installer_output" folder.
start "" "installer_output"
pause
exit /b 0

:noinno
echo.
echo Inno Setup was not found. Install it, then run this file again:
echo   winget install JRSoftware.InnoSetup
echo   (or download it from https://jrsoftware.org/isdl.php)
pause
exit /b 1

:fail
echo.
echo Something went wrong. Read the messages above.
pause
exit /b 1
