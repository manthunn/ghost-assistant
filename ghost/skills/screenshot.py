"""Take and save screenshots.

Distinct from vision.look_at_screen, which grabs a frame purely to push into the
Live session so Ghost can *see* it. This one produces an actual file the user
keeps.

Two modes, matching what Windows gives you from the keyboard:
  window  - just the focused window, the Alt+PrintScreen equivalent (default)
  full    - the whole screen, the PrintScreen equivalent

Implemented as a direct region grab rather than by sending Alt+PrintScreen and
reading the clipboard. Same picture, but the keystroke route depends on the
foreground app not swallowing the key, on clipboard timing, and on the image
surviving a clipboard round-trip - three ways to fail silently. It also
clobbers whatever the user had copied. A grab of the window's rectangle has
none of those problems.
"""
import time
from datetime import datetime
from pathlib import Path

from . import register

GHOST_WINDOW = "Ghost"   # ui3d creates a fullscreen frameless window with this title


def _pictures_dir():
    """The real Pictures folder, which is OneDrive-redirected on this machine.

    Path.home()/"Pictures" silently creates a second, non-synced folder next to
    the redirected one, so read the actual location out of the shell folders.
    """
    try:
        import os
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
            val, _ = winreg.QueryValueEx(k, "My Pictures")
        p = Path(os.path.expandvars(val))
        if p.is_dir():
            return p
    except Exception:
        pass
    return Path.home() / "Pictures"


SAVE_DIR = _pictures_dir() / "Ghost Screenshots"


def _unique(path):
    """Two screenshots in the same second must not overwrite each other."""
    if not path.exists():
        return path
    for n in range(2, 100):
        alt = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not alt.exists():
            return alt
    return path.with_name(f"{path.stem}_{int(time.time() * 1000)}{path.suffix}")


def _user32():
    """user32 with explicit signatures.

    ctypes defaults an unprototyped function's return to C int, which TRUNCATES
    a 64-bit HWND. The truncated handle then silently fails every call it is
    passed to - GetWindowTextW returns an empty string and the rect comes back
    as nonsense rather than raising. Declaring the types is not optional here.
    """
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetForegroundWindow.argtypes = []
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.IsIconic.restype = wintypes.BOOL
    u.IsIconic.argtypes = [wintypes.HWND]
    return u


def _foreground_rect():
    """(left, top, right, bottom) of the focused window, or None.

    Prefers the DWM extended frame bounds: GetWindowRect on Windows 10/11
    includes the invisible resize border, which captures as a dead margin of
    desktop around the window.
    """
    import ctypes
    from ctypes import wintypes

    u = _user32()
    hwnd = u.GetForegroundWindow()
    if not hwnd or u.IsIconic(hwnd):   # minimised windows have no useful rect
        return None

    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.restype = ctypes.HRESULT
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                          ctypes.c_void_p, wintypes.DWORD]
    rect = wintypes.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    try:
        dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                  ctypes.byref(rect), ctypes.sizeof(rect))
    except OSError:
        rect = wintypes.RECT()
    if rect.right <= rect.left or rect.bottom <= rect.top:
        if not u.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

    box = (rect.left, rect.top, rect.right, rect.bottom)
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _foreground_title():
    import ctypes
    u = _user32()
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return ""
    n = u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value or ""


def _slug(text, limit=40):
    keep = [c if (c.isalnum() or c in " -_") else " " for c in (text or "")]
    out = "-".join("".join(keep).split())
    return out[:limit].strip("-").lower()


def take_screenshot(mode="window", delay=0.0):
    """Returns (Path, description) or raises."""
    from PIL import ImageGrab

    if delay:
        time.sleep(max(0.0, min(float(delay), 20.0)))

    title = _foreground_title()
    box = None
    note = ""

    if mode == "window":
        if title == GHOST_WINDOW:
            # Ghost's own fullscreen UI is focused - capturing it returns a
            # picture of Ghost, which is never what was wanted.
            box, title = None, ""
            note = (" Ghost's own window was in front, so this is the whole screen "
                    "- suggest passing a delay and switching to the right window.")
        else:
            box = _foreground_rect()
            if box is None:
                title = ""
                note = " No focused window was available, so this is the whole screen."

    # all_screens so a window on a secondary monitor isn't captured as black.
    img = ImageGrab.grab(bbox=box, all_screens=True)
    if img.mode != "RGB":
        img = img.convert("RGB")

    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    name = stamp + (f"_{_slug(title)}" if (box and title) else "_screen")
    path = _unique(SAVE_DIR / f"{name}.png")
    img.save(path, format="PNG")

    what = f"the '{title}' window" if (box and title) else "the full screen"
    return path, f"{what}, {img.width}x{img.height}.{note}"


@register({"name": "take_screenshot",
    "description": "Take a screenshot and save it as a PNG file. Use when the user "
                    "asks to take/grab/capture a screenshot or 'screenshot this'. "
                    "Default captures just the focused window (the Alt+PrintScreen "
                    "equivalent); pass mode='full' for the entire screen. This SAVES "
                    "an image file - to instead look at the screen and answer a "
                    "question about it, use look_at_screen.",
    "parameters": {"type": "object", "properties": {
        "mode": {"type": "string", "enum": ["window", "full"],
                  "description": "'window' for the focused window (default), 'full' "
                                 "for the whole screen"},
        "delay": {"type": "integer",
                   "description": "seconds to wait before capturing, so the user can "
                                  "bring something to the front first. Default 0."}},
        "required": []}})
def take_screenshot_tool(mode: str = "window", delay: int = 0):
    mode = (mode or "window").strip().lower()
    if mode not in ("window", "full"):
        mode = "window"
    try:
        path, what = take_screenshot(mode, delay)
    except Exception as e:
        return f"Couldn't take the screenshot: {e}"
    return (f"Saved a screenshot of {what} to {path}. "
            f"Tell the user it's saved and where, briefly - don't read the full path "
            f"out character by character.")
