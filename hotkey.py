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

import ctypes
import datetime
import pathlib
import subprocess
import threading
import time
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

VK_F12 = 0x7B
POLL_SECS = 0.04
DEBOUNCE = 1.5      # one physical press must not fire hook AND poll
_last_fire = 0.0
_fire_lock = threading.Lock()


def _fallback_poll():
    """Watch F12 directly, as a backstop for the keyboard hook dying.

    Windows silently removes a low-level keyboard hook whose callback overruns
    LowLevelHooksTimeout, and it never re-arms - the listener keeps running and
    simply stops seeing keys, which looks exactly like "the hotkey randomly
    doesn't work". Elevated foreground windows can block hook delivery too.

    GetAsyncKeyState reads the key state directly rather than depending on the
    hook chain, so it still fires in both cases. Which path fired is logged, so
    the log now distinguishes "hook is dead" from "he never pressed it" - a
    distinction the old log could not make at all.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    was_down = False
    while True:
        try:
            down = bool(user32.GetAsyncKeyState(VK_F12) & 0x8000)
            if down and not was_down:
                launch("F12 (fallback poll - the keyboard hook did not fire)")
            was_down = down
        except Exception:
            pass
        time.sleep(POLL_SECS)


def launch(which="F12"):
    # Debounced because the hook and the poll both watch the same key; a single
    # press must not start Ghost twice.
    global _last_fire
    with _fire_lock:
        if time.monotonic() - _last_fire < DEBOUNCE:
            return False
        _last_fire = time.monotonic()
    return _launch(which)


def _launch(which="F12"):
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
    threading.Thread(target=_fallback_poll, daemon=True).start()
    log("listener armed - " + " or ".join(h.upper() for h in armed) + " (+ fallback poll)")
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        log("listener stopped")

if __name__ == "__main__":
    main()
