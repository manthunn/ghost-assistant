"""Background listener that brings Ghost online with a keypress.

Runs silently and arms F12 globally, so Ghost can be summoned from anywhere -
desktop, browser, a game - without a terminal open. Ghost closes itself after
its idle timeout, so the loop is press-F12 -> talk -> it goes quiet -> F12 again.

Start it once:            py hotkey.py
Arm it at every login:    py install_startup.py

Because this is normally launched with pythonw.exe (no console), print() goes
nowhere - everything is logged to hotkey.log next to this file instead.
"""
import sys

if sys.stdout is not None:                     # None under pythonw.exe
    sys.stdout.reconfigure(encoding="utf-8")

import datetime
import pathlib
import subprocess
import keyboard

# F12 alone is unreliable: browsers claim it for DevTools and VS Code for
# Go-to-Definition, so the focused app swallows it before the global hook sees
# it. Ctrl+Alt+G is the dependable one; F12 stays for when nothing has grabbed it.
HOTKEYS = ["ctrl+alt+g", "ctrl+shift+f12", "f12"]
ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"
LOG = ROOT / "hotkey.log"

_proc = None

def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if sys.stdout is not None:
        print(line)

def launch():
    global _proc
    if _proc is not None and _proc.poll() is None:
        log("F12 pressed - Ghost is already running, ignoring.")
        return False
    log(f"F12 pressed - starting Ghost ({MAIN.name})")
    # Use the console python for Ghost itself so its output stays visible, even
    # when this listener is running windowless under pythonw.exe.
    exe = pathlib.Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            exe = console
    try:
        _proc = subprocess.Popen([str(exe), str(MAIN)], cwd=str(ROOT))
        return True
    except Exception as e:
        log(f"failed to start Ghost: {e}")
        return False

def main():
    if not MAIN.exists():
        log(f"Can't find {MAIN} - run this from the ghost-assistant folder.")
        return
    armed = []
    for hk in HOTKEYS:
        try:
            keyboard.add_hotkey(hk, launch)
            armed.append(hk)
        except Exception as e:
            log(f"couldn't register {hk}: {e}")
    if not armed:
        log("no hotkeys could be registered - giving up")
        return
    log("listener armed - " + " or ".join(h.upper() for h in armed))
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        log("listener stopped")

if __name__ == "__main__":
    main()
