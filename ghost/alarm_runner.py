"""Standalone alerter for one timer or alarm. Not imported by Ghost.

    pythonw alarm_runner.py <epoch_seconds> <label>

Runs as its own detached process on purpose. Ghost closes itself after five
minutes of silence, so anything living inside Ghost's process would take a
twenty-minute timer down with it. This sleeps, alerts, and exits.

Alerting is a message box plus beeps rather than a toast notification: toasts
need a package that isn't installed, can be silently suppressed by Focus
Assist, and vanish on their own. A dialog does not go away until acknowledged,
which is the entire point of an alarm.
"""
import ctypes
import sys
import time


def alert(label):
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(880, 220)
            winsound.Beep(660, 220)
    except Exception:
        pass
    MB_OK, MB_ICONINFO, MB_TOPMOST, MB_SETFOREGROUND = 0x0, 0x40, 0x40000, 0x10000
    ctypes.windll.user32.MessageBoxW(
        None, label or "Time's up.", "Ghost",
        MB_OK | MB_ICONINFO | MB_TOPMOST | MB_SETFOREGROUND)


def main():
    if len(sys.argv) < 2:
        return 1
    target = float(sys.argv[1])
    label = " ".join(sys.argv[2:]) or "Time's up."
    while True:
        remaining = target - time.time()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 30))   # short sleeps so a killed process dies promptly
    alert(label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
