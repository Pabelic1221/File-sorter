@echo off
REM Builds FolderSorter.exe - one portable file with everything inside.
REM Run this ONCE on a Windows PC that has Python installed (python.org).
python -m pip install --upgrade pywebview pyinstaller
python -m PyInstaller --onefile --noconsole --collect-all webview --name FolderSorter foldersorter.py
echo.
echo Done! Your portable app is: dist\FolderSorter.exe
pause
