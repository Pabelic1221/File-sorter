<div align="center">

<img src="assets/icon-512.png" alt="Folder Sorter icon" width="140">

# Folder Sorter

**Pick a folder. Preview the mess. Tidy it up in one click.**

A portable, glassmorphism-styled file organizer for Windows that sorts loose files into folders by type, with live previews and full undo.

![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white)
![Dependencies](https://img.shields.io/badge/Dependencies-bundled-4cc9a0)

<img src="docs/screenshot-dark-list.png" alt="Folder Sorter in dark mode with list view" width="860">

</div>

---

## Features

**Sorting**
- Choose any folder with the built-in browser, paste a path, use a shortcut (Desktop, Downloads, and so on), or drag a folder onto the window.
- Sort **by file type** (Images, Documents, Videos, ...) or **by extension** (one folder per PDF, PNG, MP3, ...).
- Optionally include files inside subfolders and delete subfolders left empty.
- Name clashes never overwrite anything: duplicates become `file (1).pdf`.

**Preview before anything moves**
- See every file that will move and where it will go.
- **List and grid views**, with real image thumbnails.
- Click a file for a larger preview (images, text snippets, details). Use the arrow keys to move between files.
- **Untick any file** you want to leave alone. Tick or untick whole categories at once.
- Search, and filter by clicking a category tile.

**Safe by design**
- Live progress bar with a **Stop** button.
- **Multi-level undo**: the last 20 sorts are remembered per folder, and each Undo steps back one.
- Refuses drive roots, your home folder itself, and system folders.
- Hidden files, symlinks and the app itself are skipped.

**Polish**
- Glassmorphism interface with hover light, lift and glow effects.
- **Light, Dark, Auto or Clear** mode plus six color themes: Aurora, Sunset, Ocean, Forest, Orchid, Graphite.
- **Clear mode**: tinted, frosted glass that blurs your own desktop wallpaper behind the window, with a transparency slider (see below).
- Custom title bar and window controls.
- Remembers your last folder, options, view and theme.

<div align="center">
<img src="docs/screenshot-light-grid.png" alt="Light mode with grid view" width="420">
<img src="docs/screenshot-preview.png" alt="File preview window" width="420">
</div>

### Clear (transparent) theme

Open **Theme**, choose **Clear**, and drag the **Transparency** slider. The window shows a blurred, tinted copy of your desktop wallpaper behind the glass panels, lined up with the screen so it follows the window as you move it. A low slider value gives darker, more tinted glass; a high value gives lighter, clearer glass.

<div align="center">
<img src="docs/screenshot-clear.png" alt="Clear theme over a sample wallpaper" width="640">
<br><sub>Clear theme, shown with a sample wallpaper.</sub>
</div>

It reads your current wallpaper (Windows only) and shows the wallpaper only, not desktop icons or other open windows. If no wallpaper can be found, Clear mode falls back to extra-light glass over the app's colored background.

## Quick start

You need [Python 3.8+](https://www.python.org/downloads/) on Windows 10 or 11 (Edge WebView2 is built in).

**Run from source**

```bat
run_windows.bat
```

or manually:

```bash
pip install pywebview
python foldersorter.py
```

**Build a portable `.exe`**

```bat
build_windows.bat
```

This creates `dist\FolderSorter.exe`: a single file with everything inside. Copy it anywhere, even a USB stick. It does not need Python or an installer on the other PC.

> Windows SmartScreen may warn about an unsigned app the first time. Click **More info**, then **Run anyway**. Code-signing the exe removes the warning.

macOS and Linux: `build_unix.sh` builds a single executable, but the app has been developed and tested mainly against Windows.

## How to use

1. **Choose a folder** with Browse, a shortcut chip, a pasted path, or drag and drop.
2. **Pick a sort method** and any options. The preview updates automatically.
3. **Review the preview.** Untick files you want to skip.
4. Click **Sort files**. Changed your mind? Click **Undo last sort**.

## Default categories

| Folder | Extensions (examples) |
| --- | --- |
| Images | jpg, png, gif, webp, heic, svg, raw |
| Videos | mp4, mkv, mov, avi, webm |
| Audio | mp3, wav, flac, aac, m4a |
| Documents | pdf, docx, txt, md, rtf |
| Spreadsheets | xlsx, xls, csv, ods |
| Presentations | pptx, ppt, odp, key |
| Ebooks | epub, mobi, azw3 |
| Archives | zip, rar, 7z, tar, gz, iso |
| Code | py, js, html, css, json, java, cpp, sql |
| Programs | exe, msi, apk, dmg, jar |
| Fonts | ttf, otf, woff, woff2 |
| Design | psd, ai, fig, sketch, stl, dwg |
| Other | everything else |

You can edit the `CATEGORIES` dictionary at the top of `foldersorter.py` to add extensions or new folders.

## How it works

Folder Sorter runs a small local web server bound to `127.0.0.1` only, protected by a random token generated at each launch, and shows its interface in a frameless [pywebview](https://pywebview.flowrl.com/) window. If pywebview is not installed it falls back to an Edge or Chrome app window. All file operations use Python's standard library.

- **Settings:** `%USERPROFILE%\.foldersorter\settings.json`
- **Undo history:** a hidden `.foldersorter_undo.json` inside each folder you sort (delete it to forget history).
- **Nothing leaves your computer.** The app makes no internet requests, apart from opening the author's GitHub page when you click the credit link.

## Project structure

```
FolderSorter/
├── foldersorter.py      # the whole app (backend + interface)
├── icon.ico             # Windows app icon
├── assets/              # icon images
├── docs/                # README screenshots
├── run_windows.bat      # run from source
├── build_windows.bat    # build FolderSorter.exe
├── build_unix.sh        # build for macOS / Linux
└── requirements.txt
```

## Troubleshooting

- **The window has a normal title bar.** pywebview isn't installed or WebView2 is unavailable, so the app is using the fallback window. Run `pip install pywebview`.
- **Dropping a folder doesn't work.** Drag and drop needs the app window (not the fallback). Use Browse instead.
- **A file wasn't moved.** It may be open in another program, hidden, or already in the right folder. The result screen lists any files that couldn't be moved.

## Credits

Made with ❤️ by [Jushua Pabelic](https://github.com/Pabelic1221)
