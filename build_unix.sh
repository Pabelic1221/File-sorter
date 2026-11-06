#!/usr/bin/env bash
python3 -m pip install --upgrade pywebview pyinstaller
python3 -m PyInstaller --onefile --collect-all webview --name FolderSorter foldersorter.py
echo "Done! Your portable app is at dist/FolderSorter"
