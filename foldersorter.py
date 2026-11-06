#!/usr/bin/env python3
"""
Folder Sorter 2 - portable file organizer with a glassmorphism interface.

* Own frameless window with a custom title bar (uses pywebview, bundled into the exe).
* If pywebview is missing it falls back to an Edge/Chrome app window or your browser.
* Everything else is the Python standard library.
"""
import json, os, secrets, shutil, subprocess, sys, tempfile, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_NAME = "Folder Sorter"
UNDO_FILE = ".foldersorter_undo.json"
IGNORE = {"desktop.ini", "thumbs.db", ".ds_store", UNDO_FILE}
MAX_LIST = 20000
MAX_UNDO = 20

CATEGORIES = {
    "Images": "jpg jpeg png gif bmp webp tiff tif heic heif svg ico raw cr2 nef arw dng avif jfif",
    "Videos": "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts m2ts vob",
    "Audio": "mp3 wav flac aac ogg m4a wma aiff opus mid midi amr",
    "Documents": "pdf doc docx txt rtf odt md tex pages log wps",
    "Spreadsheets": "xls xlsx xlsm csv ods tsv numbers",
    "Presentations": "ppt pptx pps ppsx odp key",
    "Ebooks": "epub mobi azw azw3 djvu cbz cbr",
    "Archives": "zip rar 7z tar gz bz2 xz tgz iso img cab",
    "Code": "py js jsx tsx html htm css json xml java c cpp h hpp cs go rs php rb sh bat ps1 sql yml yaml ini toml ipynb vue kt swift lua",
    "Programs": "exe msi apk dmg pkg deb rpm appimage jar com",
    "Fonts": "ttf otf woff woff2 fon",
    "Design": "psd ai xd fig sketch indd eps blend obj fbx stl dwg dxf",
}
EXT_TO_CAT = {e: c for c, s in CATEGORIES.items() for e in s.split()}
OTHER, NO_EXT = "Other", "No extension"
IMG_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "jfif": "image/jpeg", "png": "image/png", "gif": "image/gif",
             "webp": "image/webp", "bmp": "image/bmp", "svg": "image/svg+xml", "ico": "image/x-icon", "avif": "image/avif"}
TEXT_EXTS = set("txt md csv tsv json xml html htm css js jsx tsx py log ini yml yaml toml sql sh bat ps1 java c cpp h hpp cs go rs php rb tex vue kt swift lua".split())

TOKEN = secrets.token_urlsafe(16)
last_ping = [0.0]
ALLOWED_ROOTS = set()
JOBS = {}

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".foldersorter", "settings.json")
PALETTES = ("aurora", "sunset", "ocean", "forest", "orchid", "graphite")
DEFAULTS = {"path": "", "mode": "category", "recursive": False, "removeEmpty": False,
            "view": "list", "palette": "aurora", "themeMode": "dark", "clear": 70}


# ------------------------------------------------------------------ settings
def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        for k in DEFAULTS:
            if k in d and type(d[k]) == type(DEFAULTS[k]):
                s[k] = d[k]
    except Exception:
        pass
    return s


def save_settings(d):
    s = load_settings()
    for k, v in d.items():
        if k in DEFAULTS and type(v) == type(DEFAULTS[k]):
            s[k] = v
    if s["mode"] not in ("category", "extension"): s["mode"] = "category"
    if s["view"] not in ("list", "grid"): s["view"] = "list"
    if s["palette"] not in PALETTES: s["palette"] = "aurora"
    if s["themeMode"] not in ("light", "dark", "auto", "clear"): s["themeMode"] = "dark"
    s["clear"] = max(0, min(100, s["clear"]))
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f)
    return s


# ----------------------------------------------------------------- core logic
def me_path():
    return os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)


def is_protected(p):
    p = os.path.abspath(p)
    if os.path.dirname(p) == p:
        return "Drive roots can't be sorted. Pick a folder inside it."
    if os.path.normcase(p) == os.path.normcase(os.path.expanduser("~")):
        return "Pick a folder inside your home folder (like Downloads), not the home folder itself."
    bad = []
    if os.name == "nt":
        bad = [os.environ[k] for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData") if os.environ.get(k)]
    else:
        bad = ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sys", "/usr", "/var", "/sbin",
               "/System", "/Library", "/Applications", "/opt"]
    n = os.path.normcase(p)
    for b in bad:
        b = os.path.normcase(os.path.abspath(b))
        if n == b or n.startswith(b + os.sep):
            return "That's a system folder and can't be sorted."
    return None


def check_root(path):
    if not path or not os.path.isdir(path):
        raise ValueError("That folder doesn't exist. Check the path or use Browse.")
    msg = is_protected(path)
    if msg:
        raise ValueError(msg)
    root = os.path.abspath(path)
    ALLOWED_ROOTS.add(os.path.normcase(os.path.realpath(root)))
    return root


def folder_for(name, mode):
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    if mode == "extension":
        if not ext:
            return NO_EXT
        clean = "".join(ch for ch in ext if ch.isalnum())
        return clean.upper() if clean else OTHER
    return EXT_TO_CAT.get(ext, OTHER)


def build_plan(root, recursive, mode):
    moves, skipped = [], 0
    me = me_path()

    def consider(dirpath, entry):
        nonlocal skipped
        name = entry.name
        if name.startswith(".") or name.lower() in IGNORE or entry.is_symlink() or os.path.abspath(entry.path) == me:
            skipped += 1
            return
        cat = folder_for(name, mode)
        if os.path.normcase(os.path.abspath(dirpath)) == os.path.normcase(os.path.join(root, cat)):
            skipped += 1
            return
        try:
            st = entry.stat()
            size, mt = st.st_size, st.st_mtime
        except OSError:
            size, mt = 0, 0
        moves.append((entry.path, cat, size, mt))

    def walk_dir(dp):
        try:
            with os.scandir(dp) as it:
                for e in sorted(it, key=lambda x: x.name.lower()):
                    if e.is_file(follow_symlinks=False) or (e.is_symlink() and not e.is_dir()):
                        consider(dp, e)
        except OSError:
            pass

    if recursive:
        for dp, dns, _ in os.walk(root):
            dns[:] = sorted(d for d in dns if not d.startswith("."))
            walk_dir(dp)
    else:
        walk_dir(root)
    return moves, skipped


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def scan(path, recursive, mode):
    root = check_root(path)
    moves, skipped = build_plan(root, recursive, mode)
    cats = {}
    for _, cat, size, _ in moves:
        c = cats.setdefault(cat, {"name": cat, "count": 0, "size": 0})
        c["count"] += 1
        c["size"] += size
    files = [{"p": s, "n": os.path.basename(s), "c": c, "s": z, "m": m} for s, c, z, m in moves[:MAX_LIST]]
    return {"total": len(moves), "skipped": skipped, "truncated": len(moves) > MAX_LIST,
            "cats": sorted(cats.values(), key=lambda c: -c["count"]), "files": files}


def read_log(root):
    try:
        with open(os.path.join(root, UNDO_FILE), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("sorts"), list):
            return d
    except Exception:
        pass
    return {"sorts": []}


def write_log(root, log):
    p = os.path.join(root, UNDO_FILE)
    if not log["sorts"]:
        if os.path.exists(p):
            os.remove(p)
        return
    if os.name == "nt" and os.path.exists(p):
        subprocess.run(["attrib", "-h", p], creationflags=0x08000000, check=False)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(log, f)
    if os.name == "nt":
        try:
            subprocess.run(["attrib", "+h", p], creationflags=0x08000000, check=False)
        except Exception:
            pass


def sort_now(path, recursive, mode, remove_empty, exclude=(), job=None):
    root = check_root(path)
    skip = {os.path.normcase(p) for p in exclude}
    moves = [m for m in build_plan(root, recursive, mode)[0] if os.path.normcase(m[0]) not in skip]
    if job:
        job.total = len(moves)
    done, errors, created, by_cat = [], [], [], {}
    for i, (src, cat, _, _) in enumerate(moves):
        if job:
            if job.cancel:
                break
            job.current, job.done = os.path.basename(src), i
        dest_dir = os.path.join(root, cat)
        try:
            if not os.path.isdir(dest_dir):
                os.makedirs(dest_dir)
                created.append(dest_dir)
            dst = unique_path(os.path.join(dest_dir, os.path.basename(src)))
            shutil.move(src, dst)
            done.append([src, dst])
            by_cat[cat] = by_cat.get(cat, 0) + 1
        except Exception as ex:
            errors.append(f"{os.path.basename(src)}: {ex}")
    if job:
        job.done = len(done) + len(errors)
    removed = []
    if remove_empty and recursive:
        for dp, _, _ in os.walk(root, topdown=False):
            rel = os.path.relpath(dp, root)
            if rel == "." or any(part.startswith(".") for part in rel.split(os.sep)):
                continue
            try:
                if not os.listdir(dp):
                    os.rmdir(dp)
                    removed.append(dp)
            except OSError:
                pass
    if done or removed:
        log = read_log(root)
        log["sorts"].append({"moves": done, "created": created, "removed": removed, "time": time.time()})
        log["sorts"] = log["sorts"][-MAX_UNDO:]
        write_log(root, log)
    return {"moved": len(done), "folders": len(by_cat), "byCat": by_cat, "errors": errors,
            "stopped": bool(job and job.cancel)}


def undo_info(path):
    try:
        sorts = read_log(check_root(path))["sorts"]
        if sorts:
            return {"available": True, "count": len(sorts[-1]["moves"]), "sorts": len(sorts)}
    except Exception:
        pass
    return {"available": False, "count": 0, "sorts": 0}


def undo_now(path, job=None):
    root = check_root(path)
    log = read_log(root)
    if not log["sorts"]:
        raise ValueError("There's nothing to undo in this folder.")
    entry = log["sorts"].pop()
    if job:
        job.total = len(entry["moves"])
    for d in entry.get("removed", []):
        os.makedirs(d, exist_ok=True)
    restored = missing = 0
    for i, (src, dst) in enumerate(reversed(entry["moves"])):
        if job:
            job.done, job.current = i, os.path.basename(src)
        if not os.path.exists(dst):
            missing += 1
            continue
        os.makedirs(os.path.dirname(src), exist_ok=True)
        shutil.move(dst, unique_path(src))
        restored += 1
    if job:
        job.done = job.total
    for d in reversed(entry.get("created", [])):
        try:
            os.rmdir(d)
        except OSError:
            pass
    write_log(root, log)
    return {"restored": restored, "missing": missing}


class Job:
    def __init__(self):
        self.id = secrets.token_hex(6)
        self.state, self.done, self.total, self.current = "running", 0, 0, ""
        self.result = self.error = None
        self.cancel = False

    def dict(self):
        return {"state": self.state, "done": self.done, "total": self.total, "current": self.current,
                "result": self.result, "error": self.error}


def start_job(fn, *args):
    job = Job()
    JOBS[job.id] = job

    def work():
        try:
            job.result = fn(*args, job=job)
            job.state = "cancelled" if job.cancel else "done"
        except Exception as ex:
            job.error, job.state = str(ex), "error"
    threading.Thread(target=work, daemon=True).start()
    return job.id


def list_dir(path):
    if not path:
        return {"path": "", "parent": None, "dirs": [{"name": n, "path": p} for n, p in places(True)]}
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError("Folder not found.")
    dirs = []
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        dirs.append({"name": e.name, "path": e.path})
                except OSError:
                    pass
    except PermissionError:
        raise ValueError("You don't have permission to open this folder.")
    dirs.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else "", "dirs": dirs}


def places(drives_only=False):
    if os.name == "nt":
        import string
        drives = [(f"{l}:", f"{l}:\\") for l in string.ascii_uppercase if os.path.exists(f"{l}:\\")]
    else:
        drives = [("Computer", "/")]
    if drives_only:
        return drives
    home = os.path.expanduser("~")
    return [(n, os.path.join(home, n)) for n in ("Desktop", "Downloads", "Documents", "Pictures", "Videos", "Music")
            if os.path.isdir(os.path.join(home, n))]


def safe_file(path):
    rp = os.path.realpath(path)
    if not os.path.isfile(rp):
        raise ValueError("File not found.")
    n = os.path.normcase(rp)
    for r in ALLOWED_ROOTS:
        if n.startswith(r.rstrip(os.sep) + os.sep):
            return rp
    raise ValueError("Not allowed.")


def peek(path):
    rp = safe_file(path)
    ext = os.path.splitext(rp)[1].lstrip(".").lower()
    if ext in TEXT_EXTS:
        with open(rp, "rb") as f:
            data = f.read(4001)
        if b"\0" not in data:
            return {"kind": "text", "text": data[:4000].decode("utf-8", "replace"), "truncated": len(data) > 4000}
    return {"kind": "none"}


def wallpaper_path():
    p = os.environ.get("FOLDERSORTER_WALLPAPER")
    if p and os.path.isfile(p):
        return p
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(520)
        ctypes.windll.user32.SystemParametersInfoW(0x73, 520, buf, 0)  # SPI_GETDESKWALLPAPER
        p = buf.value
    except Exception:
        p = ""
    if not (p and os.path.isfile(p)):
        p = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Themes", "TranscodedWallpaper")
    return p if os.path.isfile(p) else None


def wallpaper_style():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
        style = str(winreg.QueryValueEx(k, "WallpaperStyle")[0])
        if str(winreg.QueryValueEx(k, "TileWallpaper")[0]) == "1":
            return "tile"
        return {"0": "center", "2": "stretch", "6": "contain", "10": "cover", "22": "cover"}.get(style, "cover")
    except Exception:
        return "cover"


def wall_info():
    p = wallpaper_path()
    if not p or os.path.getsize(p) > 60_000_000:
        return {"has": False}
    return {"has": True, "style": wallpaper_style(), "v": int(os.path.getmtime(p))}


def open_folder(path):
    root = check_root(path)
    if os.name == "nt":
        os.startfile(root)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", root])
    else:
        subprocess.Popen(["xdg-open", root])
    return {"ok": True}


# ------------------------------------------------------------------ web layer
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.split("/")[0] in ("text", "application") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            if q.get("t", [""])[0] != TOKEN:
                return self._send(403, "Forbidden", "text/plain")
            page = HTML.replace("__TOKEN__", TOKEN).replace("__SETTINGS__", json.dumps(load_settings()).replace("</", "<\\/"))
            return self._send(200, page, "text/html")
        if self.headers.get("X-Token") != TOKEN and q.get("t", [""])[0] != TOKEN:
            return self._json({"error": "Unauthorized"}, 403)
        g = lambda k: q.get(k, [""])[0]
        try:
            if u.path == "/api/ping":
                last_ping[0] = time.time()
                return self._json({"ok": True})
            if u.path == "/api/places":
                return self._json([{"name": n, "path": p} for n, p in places()])
            if u.path == "/api/ls":
                return self._json(list_dir(g("path")))
            if u.path == "/api/undoinfo":
                return self._json(undo_info(g("path")))
            if u.path == "/api/job":
                j = JOBS.get(g("id"))
                return self._json(j.dict() if j else {"error": "Unknown job"}, 200 if j else 404)
            if u.path == "/api/wallinfo":
                return self._json(wall_info())
            if u.path == "/api/wallpaper":
                wp = wallpaper_path()
                if not wp:
                    raise ValueError("No wallpaper")
                with open(wp, "rb") as f:
                    data = f.read()
                ct = "image/png" if data[:4] == b"\x89PNG" else "image/bmp" if data[:2] == b"BM" else \
                    "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
                return self._send(200, data, ct, {"Cache-Control": "private, max-age=600"})
            if u.path == "/api/peek":
                return self._json(peek(g("path")))
            if u.path == "/api/thumb":
                rp = safe_file(g("path"))
                ext = os.path.splitext(rp)[1].lstrip(".").lower()
                if ext not in IMG_TYPES or os.path.getsize(rp) > 25_000_000:
                    raise ValueError("No preview")
                with open(rp, "rb") as f:
                    data = f.read()
                return self._send(200, data, IMG_TYPES[ext], {"Cache-Control": "private, max-age=600",
                                                              "Content-Security-Policy": "sandbox"})
        except Exception as ex:
            return self._json({"error": str(ex)}, 400)
        self._json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.headers.get("X-Token") != TOKEN:
            return self._json({"error": "Unauthorized"}, 403)
        try:
            n = int(self.headers.get("Content-Length", 0))
            b = json.loads(self.rfile.read(n) or b"{}")
            mode = b.get("mode") if b.get("mode") in ("category", "extension") else "category"
            rec, path = bool(b.get("recursive")), b.get("path", "")
            if self.path == "/api/scan":
                return self._json(scan(path, rec, mode))
            if self.path == "/api/sort":
                check_root(path)
                return self._json({"job": start_job(sort_now, path, rec, mode, bool(b.get("removeEmpty")),
                                                    [str(x) for x in b.get("exclude", [])])})
            if self.path == "/api/undo":
                check_root(path)
                return self._json({"job": start_job(undo_now, path)})
            if self.path == "/api/cancel":
                j = JOBS.get(b.get("job"))
                if j:
                    j.cancel = True
                return self._json({"ok": True})
            if self.path == "/api/open":
                return self._json(open_folder(path))
            if self.path == "/api/openurl":
                url = str(b.get("url", ""))
                if url.rstrip("/") != "https://github.com/Pabelic1221":
                    raise ValueError("Link not allowed.")
                webbrowser.open(url)
                return self._json({"ok": True})
            if self.path == "/api/settings":
                return self._json(save_settings(b))
            if self.path == "/api/quit":
                self._json({"ok": True})
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return
        except Exception as ex:
            return self._json({"error": str(ex)}, 400)
        self._json({"error": "Not found"}, 404)


# -------------------------------------------------------------- window launch
class WindowApi:
    """Methods the page calls for the custom title bar."""
    def __init__(self):
        self._w = None
        self._max = False

    def minimize(self):
        self._w.minimize()

    def toggle_maximize(self):
        if self._max:
            self._w.restore()
        else:
            self._w.maximize()
        self._max = not self._max
        return self._max

    def close(self):
        self._w.destroy()

    def size(self):
        return [self._w.width, self._w.height]

    def resize(self, w, h):
        self._w.resize(max(940, int(w)), max(640, int(h)))


def run_native(url):
    import webview
    api = WindowApi()
    window = webview.create_window(APP_NAME, url + "&native=1", width=1200, height=800, min_size=(940, 640),
                                   frameless=True, easy_drag=False, background_color="#0b1020", js_api=api)
    api._w = window

    def on_ready(win):
        try:  # dragging a folder onto the window gives us its real path
            from webview.dom import DOMEventHandler

            def on_drop(e):
                files = e["dataTransfer"]["files"]
                if files:
                    p = files[0].get("pywebviewFullPath")
                    if p:
                        if not os.path.isdir(p):
                            p = os.path.dirname(p)
                        win.evaluate_js("window.onDropPath(%s)" % json.dumps(p))
            win.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception:
            pass
    webview.start(on_ready, window)


def find_app_browser():
    cands = []
    if sys.platform.startswith("win"):
        for base in filter(None, [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")]):
            cands += [os.path.join(base, r"Microsoft\Edge\Application\msedge.exe"),
                      os.path.join(base, r"Google\Chrome\Application\chrome.exe"),
                      os.path.join(base, r"BraveSoftware\Brave-Browser\Application\brave.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        cands += [shutil.which(x) for x in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser")]
    return next((c for c in cands if c and os.path.exists(c)), None)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{srv.server_port}/?t={TOKEN}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    last_ping[0] = time.time()
    if os.environ.get("FOLDERSORTER_NOUI"):
        print(url, flush=True)
        while True:
            time.sleep(60)
    try:
        import webview  # noqa: F401
        run_native(url)
        os._exit(0)
    except Exception as ex:  # no pywebview / no web runtime -> browser fallback
        print("Native window unavailable (%s); using browser window." % ex)
    exe = find_app_browser()
    opened = False
    if exe:
        try:
            prof = os.path.join(tempfile.gettempdir(), "FolderSorterProfile")
            subprocess.Popen([exe, f"--app={url}", "--window-size=1200,800", f"--user-data-dir={prof}",
                              "--no-first-run", "--no-default-browser-check"])
            opened = True
        except Exception:
            pass
    if not opened:
        webbrowser.open(url)
    print(f"{APP_NAME} is running. Close the window (or press Ctrl+C) to quit.")
    try:
        while time.time() - last_ping[0] < 45:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    os._exit(0)


HTML = r"""<!doctype html>
<html lang="en" data-palette="aurora" data-mode="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Folder Sorter</title>
<link rel="icon" type="image/png" href="data:image/png;base64,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">
<style>
[data-palette=aurora]{--o1:#3b5bff;--o2:#10c9a8;--o3:#b05cff;--a1:#59e0c5;--a2:#7a97ff;--at-d:#7ee7d3;--at-l:#0a7f6c}
[data-palette=sunset]{--o1:#ff6a3d;--o2:#ff3d81;--o3:#ffb347;--a1:#ffb36b;--a2:#ff6f91;--at-d:#ffc58a;--at-l:#b4400f}
[data-palette=ocean]{--o1:#0a7cff;--o2:#00d4ff;--o3:#0047b3;--a1:#5ad1ff;--a2:#4d8dff;--at-d:#8fe0ff;--at-l:#0b62b8}
[data-palette=forest]{--o1:#1faa59;--o2:#a8e063;--o3:#0b6b5e;--a1:#8be28f;--a2:#3fc7a0;--at-d:#a9f0a8;--at-l:#167a3c}
[data-palette=orchid]{--o1:#a23bff;--o2:#ff5fb7;--o3:#5b5bff;--a1:#d58bff;--a2:#ff8fcf;--at-d:#e2b3ff;--at-l:#8a2bc4}
[data-palette=graphite]{--o1:#5b6478;--o2:#8a93a6;--o3:#3c4356;--a1:#c3cad9;--a2:#8fa0c4;--at-d:#d5dbea;--at-l:#3d4a66}
[data-mode=dark]{color-scheme:dark;--bg:color-mix(in srgb,var(--o1) 14%,#070a14);--ink:#f4f7ff;--ink2:rgba(232,238,255,.76);--ink3:rgba(232,238,255,.52);
  --glass:rgba(255,255,255,.06);--glass2:rgba(255,255,255,.14);--edge:rgba(255,255,255,.2);--well:rgba(0,0,0,.18);--field:rgba(255,255,255,.07);--sheen:rgba(255,255,255,.13);--glow:rgba(255,255,255,.34);
  --shadow:0 18px 50px rgba(2,6,24,.5);--hi:rgba(255,255,255,.28);--ring:transparent;--orb:.7;--accent-text:var(--at-d);--scrim:rgba(6,10,26,.5);--pop:rgba(28,36,70,.58)}
[data-mode=light]{color-scheme:light;--bg:color-mix(in srgb,var(--o1) 9%,#f3f6fc);--ink:#141b34;--ink2:rgba(20,27,52,.74);--ink3:rgba(20,27,52,.52);
  --glass:rgba(255,255,255,.34);--glass2:rgba(255,255,255,.66);--edge:rgba(255,255,255,.85);--well:rgba(20,27,52,.05);--field:rgba(255,255,255,.6);--sheen:rgba(255,255,255,.6);--glow:rgba(255,255,255,1);
  --shadow:0 16px 40px rgba(40,60,120,.16);--hi:rgba(255,255,255,.9);--ring:rgba(30,40,80,.08);--orb:.5;--accent-text:var(--at-l);--scrim:rgba(225,232,248,.5);--pop:rgba(255,255,255,.66)}
[data-mode=clear]{color-scheme:dark;--bg:color-mix(in srgb,var(--o1) 12%,#0a0f1e);--blur:38px;--ink:#fff;--ink2:rgba(255,255,255,.84);--ink3:rgba(255,255,255,.62);
  --glass:rgba(255,255,255,calc(.02 + (100 - var(--clear,70)) * .0009));--glass2:rgba(255,255,255,.14);--edge:rgba(255,255,255,.26);--well:rgba(0,0,0,.2);--field:rgba(255,255,255,.08);
  --shadow:0 18px 50px rgba(0,0,0,.35);--hi:rgba(255,255,255,.34);--ring:transparent;--orb:.5;--accent-text:var(--at-d);--scrim:rgba(5,8,20,.4);--pop:rgba(20,26,52,.5);--sheen:rgba(255,255,255,.16);--glow:rgba(255,255,255,.42)}
[data-mode=clear] body{text-shadow:0 1px 8px rgba(0,0,0,.3)}
[data-mode=clear] .btn.primary,[data-mode=clear] .tag,[data-mode=clear] .thumb .ext{text-shadow:none}
#wall,#walltint{position:fixed;display:none;pointer-events:none}
#wall{left:0;top:0;z-index:0;background-repeat:no-repeat;background-position:center;filter:blur(26px) saturate(1.25);will-change:transform}
#walltint{inset:0;z-index:0;background:rgba(8,12,28,var(--tint,.35))}
html[data-wall="1"] #wall,html[data-wall="1"] #walltint{display:block}
html[data-wall="1"] .orb{display:none}
input[type=range]{width:100%;accent-color:var(--a2);cursor:pointer}
:root{--font:"Segoe UI Variable Display","Segoe UI",system-ui,-apple-system,"SF Pro Display","Helvetica Neue",Roboto,sans-serif}
*{box-sizing:border-box;margin:0;scrollbar-width:thin;scrollbar-color:var(--ink3) transparent}
html,body{height:100%}
body{font-family:var(--font);color:var(--ink);background:var(--bg);overflow:hidden;font-size:14.5px;transition:background .3s}
.orb{position:fixed;border-radius:50%;filter:blur(80px);opacity:var(--orb);pointer-events:none;transition:background .4s}
.o1{width:560px;height:560px;background:var(--o1);top:-180px;left:-140px}
.o2{width:500px;height:500px;background:var(--o2);bottom:-190px;right:-100px}
.o3{width:360px;height:360px;background:var(--o3);top:34%;left:46%;opacity:calc(var(--orb) * .65)}
.glass{background:var(--glass);backdrop-filter:blur(var(--blur,26px)) saturate(170%);-webkit-backdrop-filter:blur(var(--blur,26px)) saturate(170%);
  border:1px solid var(--edge);border-radius:22px;box-shadow:var(--shadow),inset 0 1px 0 var(--hi),0 0 0 1px var(--ring)}
.window{position:relative;height:100%;display:flex;flex-direction:column}
.hidden{display:none!important}

/* title bar */
.titlebar{height:46px;flex:none;display:flex;align-items:center;background:var(--glass);backdrop-filter:blur(var(--blur,26px)) saturate(170%);
  -webkit-backdrop-filter:blur(var(--blur,26px)) saturate(170%);border-bottom:1px solid var(--edge);box-shadow:inset 0 -1px 0 var(--ring);user-select:none;z-index:6}
.brand{display:flex;align-items:center;gap:10px;padding-left:14px;height:100%}
.brand b{font-size:14px;font-weight:650;letter-spacing:-.005em}
.logo{width:28px;height:28px;border-radius:8px;overflow:hidden;filter:drop-shadow(0 2px 6px rgba(0,0,0,.25))}.logo img{width:100%;height:100%;display:block}
.spacer{flex:1;height:100%}
.tb{height:32px;margin-right:6px;padding:0 12px;border-radius:10px;border:1px solid transparent;background:transparent;color:var(--ink);font:inherit;font-size:13px;font-weight:600;
  display:flex;align-items:center;gap:7px;cursor:pointer;transition:background .15s}
.tb:hover{background:var(--glass2);border-color:var(--edge)}
.wcs{display:none;height:100%}
[data-native="1"] .wcs{display:flex}
[data-native="1"] #quit{display:none}
.wc{width:48px;height:100%;border:0;background:transparent;color:var(--ink);display:grid;place-items:center;cursor:pointer;transition:background .12s}
.wc:hover{background:var(--glass2)}
.wc.close:hover{background:#e5484d;color:#fff}

/* layout */
.body{flex:1;min-height:0;display:grid;grid-template-columns:330px 1fr;gap:14px;padding:14px 14px 8px}
.panel{padding:20px;display:flex;flex-direction:column;gap:18px;overflow:auto}
.lbl{font-size:13px;color:var(--ink2);font-weight:650;margin-bottom:8px}
.pathrow{display:flex;gap:8px}
input[type=text],input[type=search]{flex:1;min-width:0;background:var(--field);border:1px solid var(--edge);color:var(--ink);border-radius:12px;padding:10px 13px;font:inherit;font-size:14px;outline:none;transition:.15s}
input::placeholder{color:var(--ink3)}
input[type=text]:focus,input[type=search]:focus{border-color:var(--a2);box-shadow:0 0 0 3px color-mix(in srgb,var(--a2) 28%,transparent)}
.btn{background:var(--glass2);color:var(--ink);border:1px solid var(--edge);border-radius:12px;padding:10px 16px;font:inherit;font-weight:600;font-size:14px;cursor:pointer;transition:filter .15s,transform .1s,opacity .15s,background .15s;white-space:nowrap}
.btn:hover:not(:disabled){filter:brightness(1.08)}
.btn:active:not(:disabled){transform:scale(.97)}
.btn:disabled{opacity:.42;cursor:not-allowed}
.btn.primary{background:linear-gradient(135deg,var(--a1),var(--a2));color:#06122b;border-color:rgba(255,255,255,.55)}
.btn.sm{padding:6px 12px;font-size:13px;border-radius:10px}
:focus-visible{outline:2px solid var(--a2);outline-offset:2px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{background:var(--field);border:1px solid var(--edge);color:var(--ink);border-radius:99px;padding:4px 12px;font:inherit;font-size:12.5px;cursor:pointer}
.chip:hover{background:var(--glass2)}
.hint{color:var(--ink3);font-size:12.5px;line-height:1.5;margin-top:8px}
.seg{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;background:var(--well);border-radius:13px;padding:4px;gap:4px}
.seg button{background:transparent;border:0;color:var(--ink2);padding:8px;border-radius:10px;font:inherit;font-weight:600;font-size:13px;cursor:pointer}
.seg button.on{background:var(--glass2);color:var(--ink);box-shadow:inset 0 1px 0 var(--hi),0 1px 3px rgba(0,0,0,.12)}
.switch{display:flex;align-items:center;gap:12px;cursor:pointer;font-size:14px;user-select:none;margin-bottom:12px}
.switch input{position:absolute;opacity:0}
.switch span{flex:none;width:42px;height:24px;border-radius:99px;background:var(--well);border:1px solid var(--edge);position:relative;transition:.2s}
.switch span::after{content:"";position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:.2s}
.switch input:checked+span{background:linear-gradient(135deg,var(--a1),var(--a2))}
.switch input:checked+span::after{left:20px}
.switch input:focus-visible+span{outline:2px solid var(--a2);outline-offset:2px}
.switch.off{opacity:.4;pointer-events:none}
.actions{margin-top:auto;display:grid;gap:10px}
.actions .btn{padding:13px}

/* results */
.results{padding:20px;display:flex;flex-direction:column;gap:12px;min-height:0;min-width:0}
.rhead h2{font-size:20px;font-weight:650;letter-spacing:-.01em}
.rhead p{color:var(--ink2);font-size:13.5px;margin-top:3px}
.tiles{display:flex;gap:10px;overflow-x:auto;padding:2px 2px 8px;flex:none}
.tile{--c:#888;flex:none;width:156px;background:var(--glass);border:1px solid var(--edge);border-radius:16px;padding:11px 12px;position:relative;overflow:hidden;cursor:pointer;box-shadow:inset 0 1px 0 var(--hi);transition:.15s}
.tile::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--c)}
.tile.active{outline:2px solid var(--c);outline-offset:-2px}
.tile.off{opacity:.5}
.tile .top{display:flex;align-items:center;justify-content:space-between;padding-left:6px;gap:6px}
.tile b{font-size:13.5px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile .n{font-size:22px;font-weight:650;margin:3px 0 0 6px;letter-spacing:-.02em;white-space:nowrap}
.tile small{color:var(--ink2);font-size:12px;margin-left:6px}
.bar{height:4px;border-radius:9px;background:var(--well);margin:8px 0 0 6px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--c);border-radius:9px}
.toolbar{display:flex;align-items:center;gap:8px;flex:none;flex-wrap:wrap}
.search{position:relative;flex:1;min-width:150px;display:flex}
.search svg{position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--ink3);pointer-events:none}
.search input{padding-left:34px}
.vt{display:flex;background:var(--well);border-radius:11px;padding:3px;gap:2px}
.vt button{width:34px;height:30px;border:0;border-radius:8px;background:transparent;color:var(--ink2);display:grid;place-items:center;cursor:pointer}
.vt button.on{background:var(--glass2);color:var(--ink);box-shadow:inset 0 1px 0 var(--hi)}
.selinfo{font-size:12.5px;color:var(--ink2);flex:none}
.files{flex:1;min-height:0;overflow:auto;border-radius:16px;background:var(--well);padding:6px}
.files.list{display:flex;flex-direction:column;gap:2px}
.files.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));grid-auto-rows:max-content;gap:10px;align-content:start;padding:10px}
.none,.sentinel{grid-column:1/-1;padding:24px;text-align:center;color:var(--ink3)}
.sentinel{padding:1px}
.row{display:grid;grid-template-columns:22px 40px minmax(0,1fr) 74px auto;gap:12px;align-items:center;padding:6px 10px;border-radius:12px;cursor:pointer;transition:background .1s}
.row:hover{background:var(--glass)}
.row.off,.card.off{opacity:.42}
.row .nm b{display:block;font-weight:550;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .sz{color:var(--ink2);font-size:12.5px;text-align:right}
.tag{--c:#888;font-size:12px;padding:3px 10px;border-radius:99px;background:color-mix(in srgb,var(--c) 24%,transparent);border:1px solid color-mix(in srgb,var(--c) 50%,transparent);white-space:nowrap;max-width:120px;overflow:hidden;text-overflow:ellipsis}
.card{position:relative;border-radius:14px;overflow:hidden;background:var(--glass);border:1px solid var(--edge);cursor:pointer;display:flex;flex-direction:column;box-shadow:inset 0 1px 0 var(--hi);transition:transform .12s}
.card .thumb{height:112px;width:100%;border-radius:0}
.card .cbw{position:absolute;top:7px;left:7px;z-index:2;background:var(--scrim);border-radius:7px;backdrop-filter:blur(8px)}
.card .meta{padding:8px 10px 10px;display:grid;gap:6px;min-width:0}
.card .meta b{font-size:13px;font-weight:550;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .meta div{display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:12px;color:var(--ink2)}
.thumb{--c:#888;position:relative;overflow:hidden;border-radius:10px;background:color-mix(in srgb,var(--c) 26%,transparent);display:grid;place-items:center;flex:none}
.thumb.sm{width:40px;height:40px}
.thumb .ext{font-weight:700;font-size:11px;letter-spacing:.02em;color:var(--ink);text-transform:lowercase}
.thumb.lg .ext{font-size:30px}
.thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .25s}
.thumb.has img{opacity:1}
.cbw{display:inline-grid;place-items:center;width:22px;height:22px;cursor:pointer}
.cb{appearance:none;-webkit-appearance:none;width:18px;height:18px;border-radius:6px;border:1.5px solid var(--ink3);background:var(--field);display:grid;place-items:center;cursor:pointer;margin:0;transition:.12s}
.cb:checked,.cb:indeterminate{background:linear-gradient(135deg,var(--a1),var(--a2));border-color:transparent}
.cb:checked::after{content:"";width:5px;height:9px;border:solid #06122b;border-width:0 2px 2px 0;transform:rotate(45deg) translate(-1px,-1px)}
.cb:indeterminate::after{content:"";width:8px;height:2px;border-radius:2px;background:#06122b}
.empty,.prog{margin:auto;text-align:center;max-width:380px;width:100%;color:var(--ink2);line-height:1.55}
.empty svg,.prog svg{opacity:.85;margin-bottom:12px}
.empty h2,.prog h2{color:var(--ink);font-size:20px;margin-bottom:6px;letter-spacing:-.01em}
.track{height:12px;border-radius:99px;background:var(--well);overflow:hidden;margin:20px 0 10px}
.track i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--a1),var(--a2));border-radius:99px;transition:width .15s}
.pcur{font-size:12.5px;color:var(--ink3);height:18px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:16px}
.btnrow{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:16px}
.bycat{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:14px}
.banner{padding:12px 14px;border-radius:14px;background:rgba(255,120,100,.16);border:1px solid rgba(255,120,100,.45);font-size:13px;line-height:1.5;margin-top:14px;text-align:left}
.spin{width:28px;height:28px;border-radius:50%;border:3px solid var(--well);border-top-color:var(--a2);animation:sp .8s linear infinite;margin:auto}
@keyframes sp{to{transform:rotate(360deg)}}

/* overlays */
.modal{position:fixed;inset:0;background:var(--scrim);display:flex;align-items:center;justify-content:center;z-index:8;backdrop-filter:blur(3px)}
.browser,.pv{background:var(--pop)}
.browser{width:min(560px,92vw);height:min(560px,86vh);display:flex;flex-direction:column;padding:20px;gap:12px}
.bpath{display:flex;gap:8px;align-items:center}
.bpath code{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:inherit;font-size:13px;color:var(--ink2);background:var(--well);padding:10px 12px;border-radius:11px}
.blist{flex:1;overflow:auto;background:var(--well);border-radius:14px;padding:6px}
.blist button{display:flex;align-items:center;gap:10px;width:100%;text-align:left;background:transparent;border:0;color:var(--ink);font:inherit;font-size:14px;padding:9px 12px;border-radius:10px;cursor:pointer}
.blist button:hover{background:var(--glass2)}
.blist p{padding:20px;text-align:center;color:var(--ink3)}
.bfoot{display:flex;justify-content:flex-end;gap:10px}
.pv{width:min(920px,94vw);height:min(560px,88vh);display:grid;grid-template-columns:minmax(0,1fr) 270px;overflow:hidden}
.stage{background:var(--well);display:grid;place-items:center;overflow:hidden;position:relative;min-width:0}
.stage img{max-width:100%;max-height:100%;object-fit:contain}
.stage pre{width:100%;height:100%;overflow:auto;padding:18px;font:12.5px/1.55 ui-monospace,Consolas,Menlo,monospace;color:var(--ink);white-space:pre-wrap;word-break:break-word}
.pvinfo{padding:22px;display:flex;flex-direction:column;gap:14px;min-width:0;overflow:auto}
.pvinfo h3{font-size:16px;font-weight:650;word-break:break-word;letter-spacing:-.01em}
.pvinfo dl{display:grid;gap:10px}
.pvinfo dt{font-size:12px;color:var(--ink3)}
.pvinfo dd{font-size:13.5px;word-break:break-word;margin-top:1px}
.pvact{margin-top:auto;display:grid;gap:8px}
.pvnav{display:flex;gap:8px}.pvnav .btn{flex:1}
.pop{position:fixed;top:52px;right:12px;width:290px;padding:16px;z-index:9;background:var(--pop);display:grid;gap:10px}
.swatches{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.sw{background:transparent;border:0;color:var(--ink);font:inherit;font-size:12px;display:grid;justify-items:center;gap:5px;cursor:pointer;padding:6px 0;border-radius:12px}
.sw i{width:38px;height:38px;border-radius:50%;border:2px solid var(--edge);box-shadow:inset 0 1px 0 rgba(255,255,255,.4)}
.sw.on{background:var(--glass2)}.sw.on i{border-color:var(--ink)}
.sw[data-p=aurora] i{background:linear-gradient(135deg,#3b5bff,#10c9a8)}
.sw[data-p=sunset] i{background:linear-gradient(135deg,#ff6a3d,#ff3d81)}
.sw[data-p=ocean] i{background:linear-gradient(135deg,#0a7cff,#00d4ff)}
.sw[data-p=forest] i{background:linear-gradient(135deg,#1faa59,#a8e063)}
.sw[data-p=orchid] i{background:linear-gradient(135deg,#a23bff,#ff5fb7)}
.sw[data-p=graphite] i{background:linear-gradient(135deg,#5b6478,#c3cad9)}
#dropov{position:fixed;inset:12px;z-index:20;border-radius:26px;border:2px dashed var(--a2);background:var(--scrim);backdrop-filter:blur(10px);display:none;place-items:center;text-align:center;font-size:19px;font-weight:650;pointer-events:none}
#dropov.on{display:grid}
#dropov small{display:block;font-weight:400;font-size:14px;color:var(--ink2);margin-top:6px}
#toast{position:fixed;bottom:22px;left:50%;transform:translate(-50%,30px);opacity:0;pointer-events:none;padding:12px 20px;border-radius:14px;transition:.3s;font-size:14px;z-index:30;max-width:90vw}
#toast.show{opacity:1;transform:translate(-50%,0)}
.grip{position:fixed;z-index:25;display:none}
[data-native="1"] .grip{display:block}
.grip.r{top:46px;right:0;bottom:8px;width:5px;cursor:ew-resize}
.grip.b{left:0;right:8px;bottom:0;height:5px;cursor:ns-resize}
.grip.br{right:0;bottom:0;width:12px;height:12px;cursor:nwse-resize}

/* hover effects: cursor-following light, lift and glow */
.glass,.tile,.card,.row{--mx:50%;--my:-40%}
.glass,.tile,.card{transition:border-color .2s,box-shadow .25s,background-color .2s,transform .18s,opacity .15s}
.glass:hover{border-color:var(--glow);background-image:radial-gradient(420px circle at var(--mx) var(--my),var(--sheen),transparent 65%);
  box-shadow:var(--shadow),inset 0 1px 0 var(--hi),0 0 0 1px var(--ring),0 0 34px color-mix(in srgb,var(--a2) 20%,transparent)}
.tile:hover,.card:hover{transform:translateY(-3px);border-color:var(--glow);background-image:radial-gradient(220px circle at var(--mx) var(--my),var(--sheen),transparent 70%);
  box-shadow:inset 0 1px 0 var(--hi),0 12px 28px rgba(5,10,40,.28),0 0 22px color-mix(in srgb,var(--c,var(--a2)) 28%,transparent)}
.row:hover{background-color:var(--glass2);background-image:radial-gradient(260px circle at var(--mx) var(--my),var(--sheen),transparent 70%)}
.btn:hover:not(:disabled){transform:translateY(-1px);border-color:var(--glow);box-shadow:0 8px 22px color-mix(in srgb,var(--a2) 32%,transparent)}
.btn.primary:hover:not(:disabled){box-shadow:0 10px 28px color-mix(in srgb,var(--a2) 55%,transparent)}
.chip{transition:transform .15s,background .15s,border-color .15s}.chip:hover{transform:translateY(-1px);border-color:var(--glow)}
.seg button{transition:background .15s,color .15s}.seg button:not(.on):hover{background:var(--glass);color:var(--ink)}
.vt button:not(.on):hover,.sw:hover{background:var(--glass)}
.blist button{transition:background .12s}
input[type=text]:hover,input[type=search]:hover{border-color:var(--glow)}
.tb,.wc{transition:background .15s,border-color .15s}
.thumb{transition:transform .2s}.card:hover .thumb{transform:scale(1.04)}.card .thumb{transform-origin:center}
.card{overflow:hidden}
/* footer credit */
.foot{flex:none;padding:2px 24px 11px;font-size:12.5px;color:var(--ink2);display:flex;align-items:center;gap:5px;user-select:none}
.foot a{color:var(--accent-text);font-weight:650;text-decoration:none;border-bottom:1px solid transparent;transition:border-color .15s,filter .15s}
.foot a:hover{border-bottom-color:currentColor;filter:brightness(1.15)}
.heart{display:inline-block;animation:beat 1.6s ease-in-out infinite}
@keyframes beat{0%,100%{transform:scale(1)}14%{transform:scale(1.22)}28%{transform:scale(1)}42%{transform:scale(1.14)}70%{transform:scale(1)}}
@media (max-width:900px){.body{grid-template-columns:1fr;overflow:auto}body{overflow:auto}.results{min-height:520px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style></head>
<body>
<div id="wall"></div><div id="walltint"></div>
<div class="orb o1"></div><div class="orb o2"></div><div class="orb o3"></div>
<div class="window">
  <header class="titlebar">
    <div class="brand pywebview-drag-region" id="drag1">
      <div class="logo"><img src="data:image/png;base64,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" alt=""></div>
      <b>Folder Sorter</b>
    </div>
    <div class="spacer pywebview-drag-region" id="drag2"></div>
    <button class="tb" id="themebtn" title="Change theme"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a9 9 0 1 0 0 18c1.4 0 2-1 1.6-2.1-.5-1.3.4-2.4 1.7-2.4H17a4 4 0 0 0 4-4C21 6.9 17 3 12 3z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/></svg>Theme</button>
    <button class="tb" id="quit">Quit</button>
    <div class="wcs">
      <button class="wc" id="wmin" title="Minimize" aria-label="Minimize"><svg width="12" height="12" viewBox="0 0 12 12"><path d="M1 6h10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg></button>
      <button class="wc" id="wmax" title="Maximize" aria-label="Maximize"></button>
      <button class="wc close" id="wclose" title="Close" aria-label="Close"><svg width="12" height="12" viewBox="0 0 12 12"><path d="M1.5 1.5l9 9M10.5 1.5l-9 9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></button>
    </div>
  </header>
  <div class="body">
    <aside class="glass panel">
      <div>
        <div class="lbl">Folder to sort</div>
        <div class="pathrow"><input type="text" id="path" placeholder="Paste a folder path or click Browse" spellcheck="false" aria-label="Folder to sort"><button id="browse" class="btn">Browse</button></div>
        <div id="places" class="chips"></div>
        <p class="hint" id="draghint">You can also drag a folder onto this window.</p>
      </div>
      <div>
        <div class="lbl">How to sort</div>
        <div class="seg" id="seg"><button data-mode="category">By file type</button><button data-mode="extension">By extension</button></div>
        <p class="hint" id="modehint"></p>
      </div>
      <div>
        <div class="lbl">Options</div>
        <label class="switch"><input type="checkbox" id="recursive"><span></span>Include files inside subfolders</label>
        <label class="switch off" id="rmrow"><input type="checkbox" id="rmempty"><span></span>Delete subfolders left empty</label>
      </div>
      <div class="actions">
        <button id="sort" class="btn primary" disabled>Sort files</button>
        <button id="undo" class="btn" disabled>Undo last sort</button>
        <p class="hint" id="undohint" style="margin:0;text-align:center"></p>
      </div>
    </aside>
    <section class="glass results" id="results"></section>
  </div>
  <footer class="foot">Made with <span class="heart" aria-label="love">❤️</span> by <a id="credit" href="https://github.com/Pabelic1221" target="_blank" rel="noopener">Jushua Pabelic</a></footer>
</div>

<div class="modal hidden" id="modal"><div class="glass browser">
  <div class="bpath"><button class="btn" id="bup">Up</button><code id="bcur"></code></div>
  <div class="blist" id="blist"></div>
  <div class="bfoot"><button class="btn" id="bcancel">Cancel</button><button class="btn primary" id="bok">Use this folder</button></div>
</div></div>
<div class="modal hidden" id="pvmodal"><div class="glass pv" id="pv"></div></div>
<div class="glass pop hidden" id="themepop"></div>
<div id="dropov"><div>Drop a folder to sort it<small>Release anywhere in this window</small></div></div>
<div id="toast" class="glass"></div>
<div class="grip r" data-g="r"></div><div class="grip b" data-g="b"></div><div class="grip br" data-g="br"></div>

<script>
const TOKEN="__TOKEN__";
const S=Object.assign({path:"",mode:"category",recursive:false,removeEmpty:false,view:"list",palette:"aurora",themeMode:"dark",clear:70},__SETTINGS__);
const NATIVE=new URLSearchParams(location.search).get("native")==="1";
if(NATIVE)document.documentElement.dataset.native="1";
const $=s=>document.querySelector(s);
const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const fmt=b=>{const u=["B","KB","MB","GB","TB"];let i=0;while(b>=1024&&i<4){b/=1024;i++}return (i?b.toFixed(1):b)+" "+u[i]};
const plural=(n,w)=>n+" "+w+(n===1?"":"s");
const dirname=p=>p.replace(/[\\/][^\\/]*$/,"");
const COLORS=["#4cc9f0","#f72585","#ffb703","#7ae582","#b388ff","#ff8c42","#00c2a8","#e5989b","#5e8bff","#ef476f","#06d6a0","#f4a261","#9aa5b8"];
const CATCOL={Images:0,Videos:1,Audio:2,Documents:3,Spreadsheets:4,Presentations:5,Ebooks:6,Archives:7,Code:8,Programs:9,Fonts:10,Design:11,Other:12,"No extension":12};
function colorFor(n){if(n in CATCOL)return COLORS[CATCOL[n]];let h=0;for(const ch of n)h=(h*31+ch.charCodeAt(0))>>>0;return COLORS[h%12]}
const IMGEXT=new Set(["jpg","jpeg","png","gif","webp","bmp","svg","ico","avif","jfif"]);
const IC={
 folder:'<svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
 check:'<svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.8"/></svg>',
 dir:'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" style="color:var(--accent-text)"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
 list:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>',
 grid:'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>',
 search:'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
 max:'<svg width="12" height="12" viewBox="0 0 12 12"><rect x="1.5" y="1.5" width="9" height="9" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>',
 restore:'<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="1.5" y="3.5" width="7" height="7" rx="1.3"/><path d="M3.5 3.5V2.3c0-.4.4-.8.8-.8h5.4c.4 0 .8.4.8.8v5.4c0 .4-.4.8-.8.8H8.5"/></svg>'
};
async function api(url,body){
  const r=await fetch(url,{method:body?"POST":"GET",headers:{"X-Token":TOKEN,"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
  const j=await r.json();if(!r.ok)throw new Error(j.error||"Something went wrong.");return j}
function toast(msg,err){const t=$("#toast");t.textContent=msg;t.style.borderColor=err?"rgba(255,140,120,.8)":"";t.classList.add("show");clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove("show"),3300)}
let st;function save(p){Object.assign(S,p);clearTimeout(st);st=setTimeout(()=>api("/api/settings",S).catch(()=>{}),300)}

/* ---------- theme ---------- */
const PALETTES=[["aurora","Aurora"],["sunset","Sunset"],["ocean","Ocean"],["forest","Forest"],["orchid","Orchid"],["graphite","Graphite"]];
const mq=matchMedia("(prefers-color-scheme: dark)");
let wallInfo=null,wallXY="";
async function setupWall(){
  const r=document.documentElement;
  if(S.themeMode!=="clear"){r.dataset.wall="0";return}
  if(wallInfo===null){try{wallInfo=await api("/api/wallinfo")}catch(e){wallInfo={has:false}}
    if(wallInfo.has){const w=$("#wall"),st=wallInfo.style;
      w.style.backgroundImage="url(/api/wallpaper?t="+TOKEN+"&v="+wallInfo.v+")";
      w.style.backgroundSize=st==="stretch"?"100% 100%":st==="center"||st==="tile"?"auto":st;
      w.style.backgroundRepeat=st==="tile"?"repeat":"no-repeat";w.style.backgroundColor="#05070f"}}
  if(!wallInfo.has||S.themeMode!=="clear"){r.dataset.wall="0";return}
  const w=$("#wall");w.style.width=screen.width+"px";w.style.height=screen.height+"px";
  const was=r.dataset.wall==="1";r.dataset.wall="1";if(!was)wallTick()}
function wallTick(){
  if(document.documentElement.dataset.wall!=="1")return;
  const x=Math.round(window.screenX-(screen.availLeft||0)),y=Math.round(window.screenY-(screen.availTop||0)),k=x+","+y;
  if(k!==wallXY){wallXY=k;$("#wall").style.transform="translate3d("+(-x)+"px,"+(-y)+"px,0)"}
  requestAnimationFrame(wallTick)}
function applyTheme(){const r=document.documentElement;r.dataset.palette=S.palette;r.dataset.mode=S.themeMode==="auto"?(mq.matches?"dark":"light"):S.themeMode;
  r.style.setProperty("--clear",S.clear);r.style.setProperty("--tint",(0.12+(100-S.clear)/100*0.62).toFixed(2));setupWall()}
mq.addEventListener("change",applyTheme);
function buildTheme(){
  const pop=$("#themepop");pop.replaceChildren();
  const seg=el("div","seg");
  [["light","Light"],["dark","Dark"],["auto","Auto"],["clear","Clear"]].forEach(([k,n])=>{const b=el("button",S.themeMode===k?"on":"",n);b.onclick=()=>{save({themeMode:k});applyTheme();buildTheme()};seg.append(b)});
  const sw=el("div","swatches");
  PALETTES.forEach(([k,n])=>{const b=el("button","sw"+(S.palette===k?" on":""));b.dataset.p=k;b.append(el("i"),el("span","",n));b.onclick=()=>{save({palette:k});applyTheme();buildTheme()};sw.append(b)});
  const parts=[el("div","lbl","Appearance"),seg];
  if(S.themeMode==="clear"){
    const lab=el("div","lbl","Transparency "+S.clear+"%");const rg=document.createElement("input");rg.type="range";rg.min=0;rg.max=100;rg.value=S.clear;rg.setAttribute("aria-label","Transparency");
    rg.oninput=()=>{S.clear=+rg.value;lab.textContent="Transparency "+rg.value+"%";applyTheme()};
    rg.onchange=()=>save({clear:+rg.value});
    parts.push(lab,rg,el("p","hint","Shows a blurred, tinted copy of your wallpaper behind the glass."))}
  parts.push(el("div","lbl","Color theme"),sw);pop.append(...parts)}
$("#themebtn").onclick=e=>{e.stopPropagation();buildTheme();$("#themepop").classList.toggle("hidden")};
$("#themepop").addEventListener("click",e=>e.stopPropagation());
document.addEventListener("click",()=>$("#themepop").classList.add("hidden"));
applyTheme();

/* ---------- window controls (native window only) ---------- */
const PW=()=>window.pywebview&&window.pywebview.api;
let isMax=false;
function setMaxIcon(){$("#wmax").innerHTML=isMax?IC.restore:IC.max;$("#wmax").title=isMax?"Restore":"Maximize"}
async function toggleMax(){if(!PW())return;try{isMax=await PW().toggle_maximize();setMaxIcon()}catch(e){}}
$("#wmin").onclick=()=>PW()&&PW().minimize();
$("#wmax").onclick=toggleMax;
$("#wclose").onclick=()=>PW()&&PW().close();
["#drag1","#drag2"].forEach(s=>$(s).addEventListener("dblclick",toggleMax));
setMaxIcon();
$("#quit").onclick=async()=>{try{await api("/api/quit",{})}catch(e){}document.body.innerHTML='<p style="margin:40vh auto 0;text-align:center;font-size:18px">Folder Sorter has closed. You can close this window.</p>'};
document.querySelectorAll(".grip").forEach(g=>g.addEventListener("pointerdown",async e=>{
  if(!PW())return;e.preventDefault();g.setPointerCapture(e.pointerId);
  const [w0,h0]=await PW().size();const x0=e.screenX,y0=e.screenY,k=g.dataset.g;let pend=false;
  const mv=ev=>{if(pend)return;pend=true;PW().resize(k==="b"?w0:w0+ev.screenX-x0,k==="r"?h0:h0+ev.screenY-y0).finally(()=>pend=false)};
  const up=()=>{g.removeEventListener("pointermove",mv);g.removeEventListener("pointerup",up)};
  g.addEventListener("pointermove",mv);g.addEventListener("pointerup",up)}));

/* ---------- state ---------- */
const HINTS={category:"Creates folders like Images, Documents and Videos.",extension:"Creates one folder per extension, like PDF, PNG and MP3."};
const PAGE=120;
let plan=null,files=[],byPath=new Map(),excluded=new Set(),filterCat=null,query="",vis=[],shown=0,io=null,tileEls={},busy=false,jobId=null,timer=null,browsePath="",pvIndex=-1;

function opts(){return{path:$("#path").value.trim(),mode:S.mode,recursive:S.recursive,removeEmpty:S.removeEmpty}}
const selCount=()=>plan?plan.total-excluded.size:0;
function setButtons(){const n=selCount();const b=$("#sort");b.disabled=busy||!(plan&&n>0);b.textContent=plan&&plan.total>0?"Sort "+plural(n,"file"):"Sort files"}
function showEmpty(title,text,icon){const r=$("#results");r.replaceChildren();const d=el("div","empty");d.innerHTML=icon||IC.folder;d.append(el("h2","",title),el("p","",text));r.append(d)}
function showLoading(){$("#results").replaceChildren(el("div","spin"))}

async function refreshUndo(){
  const p=$("#path").value.trim();let info={available:false};
  if(p){try{info=await api("/api/undoinfo?path="+encodeURIComponent(p))}catch(e){}}
  $("#undo").disabled=busy||!info.available;
  $("#undo").textContent=info.available?"Undo last sort ("+plural(info.count,"file")+")":"Undo last sort";
  $("#undohint").textContent=info.sorts>1?info.sorts+" sorts saved. Undo goes back one at a time.":""}

/* ---------- preview ---------- */
async function preview(){
  clearTimeout(timer);const o=opts();plan=null;files=[];byPath=new Map();excluded=new Set();filterCat=null;query="";setButtons();refreshUndo();
  if(!o.path){showEmpty("Choose a folder","Browse to the folder you want to organize, or drag it onto this window. You'll see a preview before anything moves.");return}
  showLoading();
  try{plan=await api("/api/scan",o);files=plan.files;byPath=new Map(files.map(f=>[f.p,f]));renderResults()}
  catch(e){showEmpty("Can't use this folder",e.message)}
  setButtons()}
const schedule=()=>{clearTimeout(timer);timer=setTimeout(preview,350)};

function thumb(f,cls){
  const d=el("div","thumb "+cls);d.style.setProperty("--c",colorFor(f.c));
  const has=f.n.includes(".");const ext=has?f.n.split(".").pop().toLowerCase():"file";
  d.append(el("span","ext",ext.slice(0,4)));
  if(has&&IMGEXT.has(ext)&&f.s<25e6){const im=new Image();im.loading="lazy";im.decoding="async";im.alt="";
    im.src="/api/thumb?t="+TOKEN+"&path="+encodeURIComponent(f.p);im.onload=()=>d.classList.add("has");im.onerror=()=>im.remove();d.append(im)}
  return d}
function checkbox(f,host){
  const l=el("label","cbw");const c=document.createElement("input");c.type="checkbox";c.className="cb";c.checked=!excluded.has(f.p);c.setAttribute("aria-label","Include "+f.n);
  c.onchange=()=>{c.checked?excluded.delete(f.p):excluded.add(f.p);host.classList.toggle("off",!c.checked);updateCounts()};l.append(c);return l}
function tag(name){const t=el("span","tag",name);t.style.setProperty("--c",colorFor(name));return t}
function rowEl(f,i){
  const r=el("div","row"+(excluded.has(f.p)?" off":""));r.dataset.p=f.p;
  const nm=el("div","nm");nm.append(el("b","",f.n));nm.title=f.p;
  r.append(checkbox(f,r),thumb(f,"sm"),nm,el("span","sz",fmt(f.s)),tag(f.c));
  r.onclick=e=>{if(!e.target.closest(".cbw"))openPreview(i)};return r}
function cardEl(f,i){
  const c=el("div","card"+(excluded.has(f.p)?" off":""));c.dataset.p=f.p;
  const m=el("div","meta");const line=el("div");line.append(el("span","",fmt(f.s)),tag(f.c));const b=el("b","",f.n);b.title=f.p;m.append(b,line);
  c.append(thumb(f,"lg"),checkbox(f,c),m);c.onclick=e=>{if(!e.target.closest(".cbw"))openPreview(i)};return c}

function visibleFiles(){const q=query.toLowerCase();return files.filter(f=>(!filterCat||f.c===filterCat)&&(!q||f.n.toLowerCase().includes(q)))}
function renderFiles(){
  const box=$("#files");box.className="files "+S.view;box.replaceChildren();if(io)io.disconnect();
  io=new IntersectionObserver(es=>{if(es[0].isIntersecting)more()},{root:box,rootMargin:"400px"});
  vis=visibleFiles();shown=0;
  if(!vis.length){box.append(el("div","none","No files match."))}else more();updateCounts()}
function more(){
  const box=$("#files");box.querySelectorAll(".sentinel").forEach(s=>{io.unobserve(s);s.remove()});
  const end=Math.min(shown+PAGE,vis.length);
  for(let i=shown;i<end;i++)box.append(S.view==="grid"?cardEl(vis[i],i):rowEl(vis[i],i));shown=end;
  if(shown<vis.length){const s=el("div","sentinel");box.append(s);io.observe(s)}}
function syncRows(){document.querySelectorAll("#files .row,#files .card").forEach(r=>{const off=excluded.has(r.dataset.p);r.classList.toggle("off",off);r.querySelector(".cb").checked=!off});updateCounts()}

function updateCounts(){
  if(!plan||!$("#sub"))return;
  const ex={};excluded.forEach(p=>{const f=byPath.get(p);if(f)ex[f.c]=(ex[f.c]||0)+1});
  let folders=0;
  plan.cats.forEach(c=>{const s=c.count-(ex[c.name]||0);if(s>0)folders++;const t=tileEls[c.name];
    if(t){t.cb.checked=s>0;t.cb.indeterminate=s>0&&s<c.count;t.n.textContent=s===c.count?c.count:s+" of "+c.count;t.root.classList.toggle("off",s===0)}});
  const n=selCount();
  $("#sub").textContent=n===0?"Nothing selected. Tick the files you want to sort.":plural(n,"file")+" will move into "+plural(folders,"folder")+"."+(plan.skipped?" "+plural(plan.skipped,"file")+" skipped.":"");
  $("#selinfo").textContent="Showing "+vis.length+" of "+files.length+(plan.truncated?" (first "+files.length+")":"");
  setButtons()}

function renderResults(){
  const r=$("#results");r.replaceChildren();tileEls={};
  if(plan.total===0){showEmpty("Already tidy","No loose files to move here."+(plan.skipped?" "+plural(plan.skipped,"file")+" skipped.":""),IC.check);return}
  const h=el("div","rhead");const sub=el("p");sub.id="sub";h.append(el("h2","","Preview"),sub);
  const tiles=el("div","tiles");const max=Math.max(...plan.cats.map(c=>c.count));
  plan.cats.forEach(c=>{
    const t=el("div","tile");t.style.setProperty("--c",colorFor(c.name));t.title="Click to show only "+c.name;
    const top=el("div","top");const cbw=el("label","cbw");const cb=document.createElement("input");cb.type="checkbox";cb.className="cb";cb.setAttribute("aria-label","Include all "+c.name);
    cb.onchange=()=>{files.forEach(f=>{if(f.c===c.name)cb.checked?excluded.delete(f.p):excluded.add(f.p)});syncRows()};cbw.append(cb);
    top.append(el("b","",c.name),cbw);
    const n=el("div","n",String(c.count));const bar=el("div","bar");const bi=el("i");bi.style.width=(c.count/max*100)+"%";bar.append(bi);
    t.append(top,n,el("small","",fmt(c.size)),bar);
    t.onclick=e=>{if(e.target.closest(".cbw"))return;filterCat=filterCat===c.name?null:c.name;Object.entries(tileEls).forEach(([k,v])=>v.root.classList.toggle("active",k===filterCat));renderFiles()};
    tileEls[c.name]={root:t,cb,n};tiles.append(t)});
  const tb=el("div","toolbar");
  const sw=el("div","search");sw.innerHTML=IC.search;const si=document.createElement("input");si.type="search";si.placeholder="Search these files";si.setAttribute("aria-label","Search files");
  si.oninput=()=>{query=si.value.trim();renderFiles()};sw.append(si);
  const all=el("button","btn sm","Select all"),none=el("button","btn sm","Select none");
  all.onclick=()=>{excluded.clear();syncRows()};none.onclick=()=>{files.forEach(f=>excluded.add(f.p));syncRows()};
  const vt=el("div","vt");[["list","List view"],["grid","Grid view"]].forEach(([k,t])=>{const b=el("button",S.view===k?"on":"");b.innerHTML=IC[k];b.title=t;b.setAttribute("aria-label",t);
    b.onclick=()=>{save({view:k});vt.querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));renderFiles()};vt.append(b)});
  tb.append(sw,all,none,vt);
  const info=el("div","selinfo");info.id="selinfo";
  const box=el("div","files");box.id="files";
  r.append(h,tiles,tb,info,box);renderFiles()}

/* ---------- file preview window ---------- */
function openPreview(i){
  pvIndex=i;const f=vis[i];if(!f)return;$("#pvmodal").classList.remove("hidden");
  const pv=$("#pv");pv.replaceChildren();
  const stage=el("div","stage");const ext=f.n.includes(".")?f.n.split(".").pop().toLowerCase():"";
  if(IMGEXT.has(ext)&&f.s<25e6){const im=new Image();im.alt=f.n;im.src="/api/thumb?t="+TOKEN+"&path="+encodeURIComponent(f.p);im.onerror=()=>{im.remove();stage.append(thumb(f,"lg"))};stage.append(im)}
  else{const big=thumb(f,"lg");big.style.cssText="width:140px;height:140px;border-radius:28px";stage.append(big);
    api("/api/peek?path="+encodeURIComponent(f.p)).then(d=>{if(pvIndex===i&&d.kind==="text"){const pre=el("pre","",d.text+(d.truncated?"\n\n…":""));stage.replaceChildren(pre)}}).catch(()=>{})}
  const info=el("div","pvinfo");const dl=el("dl");
  const add=(k,v)=>{const g=el("div");g.append(el("dt","",k),el("dd","",v));dl.append(g)};
  add("Type",(ext?ext.toUpperCase()+" file":"File"));add("Size",fmt(f.s));add("Modified",f.m?new Date(f.m*1000).toLocaleString():"Unknown");add("Now in",dirname(f.p));add("Will move to",f.c+" folder");
  const act=el("div","pvact");
  const nav=el("div","pvnav");const pb=el("button","btn sm","Previous"),nb=el("button","btn sm","Next");pb.disabled=i<=0;nb.disabled=i>=vis.length-1;
  pb.onclick=()=>openPreview(i-1);nb.onclick=()=>openPreview(i+1);nav.append(pb,nb);
  const inc=el("button","btn",excluded.has(f.p)?"Include this file":"Skip this file");
  inc.onclick=()=>{excluded.has(f.p)?excluded.delete(f.p):excluded.add(f.p);syncRows();openPreview(i)};
  const cl=el("button","btn primary","Close");cl.onclick=closePreview;
  act.append(nav,inc,cl);info.append(el("h3","",f.n),dl,act);pv.append(stage,info)}
const closePreview=()=>{$("#pvmodal").classList.add("hidden");pvIndex=-1};
$("#pvmodal").addEventListener("click",e=>{if(e.target.id==="pvmodal")closePreview()});

/* ---------- sorting jobs ---------- */
function showProgress(title){
  const r=$("#results");r.replaceChildren();const d=el("div","prog");d.innerHTML=IC.folder;
  const tr=el("div","track");const bar=el("i");bar.id="pbar";tr.append(bar);
  const p1=el("p","","Getting ready…");p1.id="ptxt";const p2=el("div","pcur");p2.id="pcur";
  const stop=el("button","btn","Stop");stop.id="pstop";stop.onclick=()=>{stop.disabled=true;stop.textContent="Stopping…";api("/api/cancel",{job:jobId}).catch(()=>{})};
  d.append(el("h2","",title),tr,p1,p2,stop);r.append(d)}
function updateProgress(j){
  if(!$("#pbar"))return;const pct=j.total?Math.round(j.done/j.total*100):0;$("#pbar").style.width=pct+"%";
  $("#ptxt").textContent=j.total?j.done+" of "+j.total+" files ("+pct+"%)":"Getting ready…";$("#pcur").textContent=j.current||""}
async function runJob(url,body,kind){
  busy=true;setButtons();$("#undo").disabled=true;showProgress(kind==="sort"?"Sorting your files":"Restoring your files");
  try{jobId=(await api(url,body)).job}catch(e){busy=false;showEmpty("Couldn't start",e.message);setButtons();refreshUndo();return}
  let j;
  while(true){await sleep(120);try{j=await api("/api/job?id="+jobId)}catch(e){j={state:"error",error:e.message};break}
    updateProgress(j);if(j.state!=="running")break}
  busy=false;jobId=null;
  if(j.state==="error"){showEmpty("Something went wrong",j.error);setButtons();refreshUndo();return}
  if(kind==="undo"){toast("Restored "+plural(j.result.restored,"file")+(j.result.missing?" ("+j.result.missing+" no longer found)":""));preview();return}
  showDone(j.result);setButtons();refreshUndo()}
function showDone(res){
  const r=$("#results");r.replaceChildren();const d=el("div","prog");d.innerHTML=IC.check;
  d.append(el("h2","",res.stopped?"Stopped early":"All sorted"),
    el("p","",res.moved?("Moved "+plural(res.moved,"file")+" into "+plural(res.folders,"folder")+". You can undo this any time."):"No files were moved."));
  const bc=el("div","bycat");Object.entries(res.byCat).forEach(([k,v])=>bc.append(tag(k+"  "+v)));d.append(bc);
  if(res.errors.length)d.append(el("div","banner","Couldn't move "+plural(res.errors.length,"file")+": "+res.errors.slice(0,3).join("; ")));
  const row=el("div","btnrow");
  const u=el("button","btn primary","Undo this sort");u.onclick=()=>runJob("/api/undo",opts(),"undo");u.disabled=!res.moved;
  const o=el("button","btn","Open folder");o.onclick=()=>api("/api/open",opts()).catch(e=>toast(e.message,1));
  const a=el("button","btn","Scan again");a.onclick=preview;row.append(u,o,a);d.append(row);r.append(d);toast("Sorted "+plural(res.moved,"file"))}
$("#sort").onclick=()=>{const o=opts();o.exclude=[...excluded];runJob("/api/sort",o,"sort")};
$("#undo").onclick=()=>runJob("/api/undo",opts(),"undo");

/* ---------- controls ---------- */
$("#path").addEventListener("input",()=>{save({path:$("#path").value.trim()});schedule()});
function syncControls(){
  $("#path").value=S.path;$("#recursive").checked=S.recursive;$("#rmempty").checked=S.removeEmpty&&S.recursive;$("#rmrow").classList.toggle("off",!S.recursive);
  document.querySelectorAll("#seg button").forEach(b=>b.classList.toggle("on",b.dataset.mode===S.mode));$("#modehint").textContent=HINTS[S.mode]}
$("#recursive").addEventListener("change",e=>{save({recursive:e.target.checked,removeEmpty:e.target.checked?S.removeEmpty:false});syncControls();schedule()});
$("#rmempty").addEventListener("change",e=>save({removeEmpty:e.target.checked}));
$("#seg").addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;save({mode:b.dataset.mode});syncControls();schedule()});

/* folder browser */
async function loadDir(p){
  try{const d=await api("/api/ls?path="+encodeURIComponent(p));browsePath=d.path;
    $("#bcur").textContent=d.path||"This computer";$("#bup").disabled=d.parent===null;$("#bup").dataset.to=d.parent||"";$("#bok").disabled=!d.path;
    const l=$("#blist");l.replaceChildren();if(!d.dirs.length)l.append(el("p","","No subfolders here."));
    d.dirs.forEach(x=>{const b=el("button");b.innerHTML=IC.dir;b.append(document.createTextNode(x.name));b.onclick=()=>loadDir(x.path);l.append(b)})}
  catch(e){toast(e.message,1)}}
$("#browse").addEventListener("click",()=>{$("#modal").classList.remove("hidden");loadDir($("#path").value.trim())});
$("#bup").addEventListener("click",()=>loadDir($("#bup").dataset.to));
$("#bcancel").addEventListener("click",()=>$("#modal").classList.add("hidden"));
$("#modal").addEventListener("click",e=>{if(e.target.id==="modal")$("#modal").classList.add("hidden")});
function choose(p){$("#path").value=p;save({path:p});preview()}
$("#bok").addEventListener("click",()=>{$("#modal").classList.add("hidden");choose(browsePath)});
document.addEventListener("keydown",e=>{
  if(e.key==="Escape"){$("#modal").classList.add("hidden");$("#themepop").classList.add("hidden");closePreview()}
  if(pvIndex>=0&&e.key==="ArrowRight"&&pvIndex<vis.length-1)openPreview(pvIndex+1);
  if(pvIndex>=0&&e.key==="ArrowLeft"&&pvIndex>0)openPreview(pvIndex-1)});

/* drag and drop a folder (real paths only available in the app window) */
let depth=0;const ov=$("#dropov");
addEventListener("dragenter",e=>{e.preventDefault();depth++;ov.classList.add("on")});
addEventListener("dragleave",()=>{depth=Math.max(0,depth-1);if(!depth)ov.classList.remove("on")});
addEventListener("dragover",e=>e.preventDefault());
addEventListener("drop",e=>{e.preventDefault();depth=0;ov.classList.remove("on");if(!NATIVE)toast("Dropping folders only works in the app window. Use Browse instead.",1)});
window.onDropPath=p=>{if(!p)return;choose(p);toast("Folder selected")};
if(!NATIVE)$("#draghint").classList.add("hidden");

/* hover light follows the cursor */
document.addEventListener("pointermove",e=>{const t=e.target.closest&&e.target.closest(".glass,.tile,.card,.row");
  if(t){const r=t.getBoundingClientRect();t.style.setProperty("--mx",(e.clientX-r.left)+"px");t.style.setProperty("--my",(e.clientY-r.top)+"px")}});
$("#credit").addEventListener("click",e=>{e.preventDefault();api("/api/openurl",{url:e.currentTarget.href}).catch(()=>window.open(e.currentTarget.href,"_blank"))});

/* start */
syncControls();
api("/api/places").then(ps=>{const c=$("#places");ps.forEach(p=>{const b=el("button","chip",p.name);b.onclick=()=>choose(p.path);c.append(b)})});
setInterval(()=>api("/api/ping").catch(()=>{}),2000);api("/api/ping").catch(()=>{});
preview();
</script></body></html>
"""

if __name__ == "__main__":
    main()
