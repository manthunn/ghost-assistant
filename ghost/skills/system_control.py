import os
import subprocess
import pathlib
from . import register

HOME = pathlib.Path.home()
FOLDERS = {"desktop": HOME / "Desktop", "downloads": HOME / "Downloads",
           "documents": HOME / "OneDrive" / "Documents"}
APPS = {"notepad": "notepad", "calculator": "calc", "chrome": "chrome",
        "spotify": "spotify", "vs code": "code", "vscode": "code",
        "edge": "msedge", "explorer": "explorer", "settings": "ms-settings:"}
DANGEROUS = ["format", "del ", "remove-item", "rm ", "rmdir", "shutdown",
             "diskpart", "reg ", "bcdedit"]

@register({"name": "open_app",
    "description": "Open an application on the PC, e.g. notepad, chrome, spotify, vs code, calculator.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "the app to open"}},
        "required": ["name"]}})
def open_app(name: str):
    cmd = APPS.get(name.lower().strip(), name)
    try:
        os.startfile(cmd)
    except OSError:
        subprocess.Popen(f'start "" "{cmd}"', shell=True)
    return f"Opened {name}."

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
    "description": "Run a PowerShell command on the PC and return its output. Use for system tasks.",
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string"}}, "required": ["command"]}})
def run_command(command: str):
    if any(d in command.lower() for d in DANGEROUS):
        print(f"\n⚠️  Ghost wants to run: {command}")
        ok = input("Type yes to allow: ")
        if ok.strip().lower() != "yes":
            return "User denied that command."
    r = subprocess.run(["powershell", "-Command", command],
                       capture_output=True, text=True, timeout=60)
    out = (r.stdout or r.stderr or "").strip()
    return out[:1500] or "Done. No output."