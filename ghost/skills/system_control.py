import os
import re
import subprocess
import pathlib
import shutil
import winreg
from . import register

HOME = pathlib.Path.home()
FOLDERS = {"desktop": HOME / "Desktop", "downloads": HOME / "Downloads",
           "documents": HOME / "OneDrive" / "Documents"}
# Friendly shortcuts for common built-in commands that don't need file resolution.
ALIASES = {"notepad": "notepad", "calculator": "calc", "chrome": "chrome",
           "spotify": "spotify", "vs code": "code", "vscode": "code",
           "edge": "msedge", "explorer": "explorer", "settings": "ms-settings:"}
# Matched as regex against the actual command shape, not bare substrings -
# a naive substring check on "format" used to false-positive on any command
# containing the word "format" at all, e.g. a URL query string "?format=json".
DANGEROUS_PATTERNS = [
    r"\bformat\s+[a-z]:",       # disk format, e.g. "format C: /q"
    r"\bdel\s",
    r"\bremove-item\b",
    r"\brm\s",
    r"\brmdir\b",
    r"\bshutdown\b",
    r"\bdiskpart\b",
    r"\breg\s+(delete|add)\b",
    r"\bbcdedit\b",
]

def _looks_dangerous(command: str) -> bool:
    low = command.lower()
    return any(re.search(p, low) for p in DANGEROUS_PATTERNS)

def _app_paths_lookup(name):
    for exe in (name, f"{name}.exe"):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(
                    hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
                path, _ = winreg.QueryValueEx(key, "")
                if path and pathlib.Path(path).exists():
                    return path
            except OSError:
                continue
    return None

def _start_menu_lookup(name):
    roots = [
        pathlib.Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
        pathlib.Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    target = name.lower().replace(" ", "")
    fallback = None
    for root in roots:
        if not root.exists():
            continue
        for shortcut in root.rglob("*.lnk"):
            stem = shortcut.stem.lower().replace(" ", "")
            if target == stem:
                return shortcut
            if target in stem and fallback is None:
                fallback = shortcut
    return fallback

def _resolve(name: str):
    key = name.lower().strip()
    if key in ALIASES:
        return ALIASES[key]
    which = shutil.which(name)
    if which:
        return which
    app_path = _app_paths_lookup(key)
    if app_path:
        return app_path
    shortcut = _start_menu_lookup(name)
    if shortcut:
        return str(shortcut)
    return None

@register({"name": "open_app",
    "description": "Open an application installed on the PC by name, e.g. notepad, chrome, vivaldi, spotify, vs code, calculator.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "the app to open"}},
        "required": ["name"]}})
def open_app(name: str):
    target = _resolve(name)
    if not target:
        return f"Couldn't find an app called '{name}' installed on this PC."
    try:
        os.startfile(target)
        return f"Opened {name}."
    except OSError as e:
        return f"Found '{name}' but couldn't launch it: {e}"

@register({"name": "list_files",
    "description": "List files in a folder: desktop, downloads, documents, or a full path.",
    "parameters": {"type": "object", "properties": {
        "folder": {"type": "string"}}, "required": ["folder"]}})
def list_files(folder: str):
    path = FOLDERS.get(folder.lower().strip(), pathlib.Path(folder))
    if not path.exists():
        return f"Folder not found: {path}"
    items = sorted(p.name for p in path.iterdir())[:40]
    return f"Contents of {path.name}: " + ", ".join(items)

@register({"name": "read_file",
    "description": "Read a text file and return its contents.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string", "description": "full file path"}},
        "required": ["path"]}})
def read_file(path: str):
    p = pathlib.Path(path)
    if not p.exists():
        return "File not found."
    return p.read_text(encoding="utf-8", errors="ignore")[:3000]

@register({"name": "write_file",
    "description": "Create or overwrite a text file with given content.",
    "parameters": {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}})
def write_file(path: str, content: str):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {p}."

@register({"name": "run_command",
    "description": "Run a PowerShell command for LOCAL system tasks: files, processes, "
                    "settings, installed programs. Do NOT use this to fetch live info from "
                    "the web or an API (weather, sports, news, prices) - use web_search or "
                    "read_webpage for that instead, they're faster and don't need confirmation.",
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string"}}, "required": ["command"]}})
def run_command(command: str):
    if _looks_dangerous(command):
        print(f"\n⚠️  Ghost wants to run: {command}")
        ok = input("Type yes to allow: ")
        if ok.strip().lower() != "yes":
            return "User denied that command."
    r = subprocess.run(["powershell", "-Command", command],
                       capture_output=True, text=True, timeout=60)
    out = (r.stdout or r.stderr or "").strip()
    return out[:1500] or "Done. No output."