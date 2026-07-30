"""Background listener that brings Ghost online with a keypress.

Run this once (ideally at login) and leave it running. Press F12 any time to
start Ghost; Ghost closes itself after its idle timeout, so the loop is
press-F12 -> talk -> it goes quiet -> press F12 again.

    py hotkey.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import pathlib
import subprocess
import keyboard

HOTKEY = "f12"
ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / "main.py"

_proc = None

def launch():
    global _proc
    if _proc is not None and _proc.poll() is None:
        print("Ghost is already running.")
        return
    print(f"Starting Ghost ({MAIN.name})...")
    # Same interpreter that's running this listener, so it can't pick up a
    # different Python that lacks the dependencies.
    _proc = subprocess.Popen([sys.executable, str(MAIN)], cwd=str(ROOT))

def main():
    if not MAIN.exists():
        print(f"Can't find {MAIN} - run this from the ghost-assistant folder.")
        return
    keyboard.add_hotkey(HOTKEY, launch)
    print(f"Ghost hotkey listener active - press {HOTKEY.upper()} to bring Ghost online.")
    print("Press Ctrl+C here to stop listening.")
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\nHotkey listener stopped.")

if __name__ == "__main__":
    main()
