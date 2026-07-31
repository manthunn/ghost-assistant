"""Arm Ghost's hotkey listener at every Windows login.

    py install_startup.py            # install
    py install_startup.py --wake     # also start the "hey ghost" wake word
    py install_startup.py --remove   # undo

Puts a shortcut in the current user's Startup folder pointing at pythonw.exe,
so the listener runs with no console window and no terminal needs to be open.
User-level only - no admin rights, no registry, no scheduled task, and it can
be undone by deleting the shortcut by hand if this script ever goes missing.
"""
import sys
import pathlib
import win32com.client

ROOT = pathlib.Path(__file__).resolve().parent
SHELL = win32com.client.Dispatch("WScript.Shell")
STARTUP = pathlib.Path(SHELL.SpecialFolders("Startup"))

TARGETS = {
    "Ghost Hotkey.lnk": ("hotkey.py", "Ghost - F12 brings the assistant online"),
    "Ghost Wake Word.lnk": ("wake.py", "Ghost - say 'hey ghost' to bring it online"),
}

def _pythonw():
    exe = pathlib.Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    if not w.exists():
        raise SystemExit(f"pythonw.exe not found next to {exe} - can't run without a console.")
    return w

def install(script, link_name, description):
    target = ROOT / script
    if not target.exists():
        print(f"  skip {link_name}: {target} not found")
        return
    lnk = STARTUP / link_name
    s = SHELL.CreateShortCut(str(lnk))
    s.TargetPath = str(_pythonw())
    s.Arguments = f'"{target}"'
    s.WorkingDirectory = str(ROOT)      # so relative paths and .env resolve
    s.Description = description
    s.WindowStyle = 7                    # minimised; pythonw shows nothing anyway
    s.Save()
    print(f"  installed {link_name} -> pythonw {script}")

def remove():
    for link_name in TARGETS:
        lnk = STARTUP / link_name
        if lnk.exists():
            lnk.unlink()
            print(f"  removed {link_name}")
        else:
            print(f"  {link_name} wasn't installed")

def main():
    args = set(sys.argv[1:])
    print(f"Startup folder: {STARTUP}")
    if "--remove" in args:
        remove()
        return
    install("hotkey.py", "Ghost Hotkey.lnk", TARGETS["Ghost Hotkey.lnk"][1])
    if "--wake" in args:
        install("wake.py", "Ghost Wake Word.lnk", TARGETS["Ghost Wake Word.lnk"][1])
    else:
        print("  (skipping wake word - pass --wake to start that at login too)")
    print()
    print("Done. F12 will bring Ghost online after your next login.")
    print("To start it right now without rebooting:  py hotkey.py")

if __name__ == "__main__":
    main()
