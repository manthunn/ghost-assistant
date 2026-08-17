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

# F12 only, by request. Ctrl+Alt+G and Ctrl+Shift+F12 were here as fallbacks
# because browsers claim F12 for DevTools and VS Code for Go-to-Definition.
#
# The low-level hook still runs ahead of the focused window, so the callback
# should fire even when an app also acts on the key. If presses stop reaching
# the log, suppress=True on add_hotkey is the fix - it stops the focused app
# seeing F12 at all. That is normally banned here because suppressing a
# ctrl+alt+* combo delays every Ctrl press (diagnosed as laggy crouch in
# Valorant, 2026-08-12), but that reasoning does not apply to a bare F12: there
# is no modifier prefix to swallow, so only F12 itself would be held. The cost
# is losing Go-to-Definition and DevTools on that key.
HOTKEYS = ["f12"]
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

def launch(which="F12"):
    # Log which key actually fired. It used to say "F12 pressed" whichever of
    # the three combos triggered it, so the log couldn't answer whether F12
    # itself was ever reaching the hook - which is exactly the question that
    # came up when it stopped working.
    global _proc
    if _proc is not None and _proc.poll() is None:
        log(f"{which} pressed - Ghost is already running, ignoring.")
        return False
    log(f"{which} pressed - starting Ghost ({MAIN.name})")
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
            keyboard.add_hotkey(hk, lambda k=hk: launch(k.upper()))
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
