import time
from . import register

# pywinauto and win32clipboard are imported lazily, inside the functions that need
# them, NOT at module level. Importing pywinauto calls CoInitialize (COM apartment
# setup); skills load on a background thread while pywebview is initialising
# WebView2's COM on the main thread, and the two deadlock - Ghost hung forever
# during startup with no window and no error. Deferring the import to first actual
# use means startup never touches COM from the wrong thread.

def _set_clipboard(text):
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()

# Any control whose name matches one of these needs a typed confirmation
# before Ghost is allowed to click/activate it - covers money, deletion,
# and anything else that can't be easily undone.
DANGEROUS_UI = [
    "delete", "remove", "uninstall", "confirm", "pay", "buy",
    "purchase", "checkout", "order now", "place order", "transfer",
    "send money", "subscribe", "sign up", "submit", "empty trash",
    "factory reset", "shut down",
]

def _desktop():
    from pywinauto import Desktop   # lazy: see note at top of file
    return Desktop(backend="uia")

def _activate(win, settle=1.5):
    """Focus a window before reading it.

    WebView2/Electron apps (new Outlook, Teams, Discord, Spotify) only populate
    their UIA accessibility tree once focused - without this they look empty,
    exposing nothing but window chrome.
    """
    try:
        win.set_focus()
        time.sleep(settle)
    except Exception:
        pass  # some windows refuse focus; reading may still partly work
    return win

def _find_window(title):
    query = title.lower().strip()
    best = None
    for w in _desktop().windows():
        text = (w.window_text() or "").lower()
        if not text:
            continue
        if query == text:
            return w
        if query in text and best is None:
            best = w
    return best

@register({"name": "list_windows",
    "description": "List titles of all open windows on the PC's desktop right now.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def list_windows():
    titles = [w.window_text() for w in _desktop().windows() if w.window_text()]
    if not titles:
        return "No open windows found."
    return "Open windows: " + ", ".join(titles[:30])

@register({"name": "list_controls",
    "description": "List the clickable controls (buttons, checkboxes, tabs, fields) inside "
                    "a specific open window, by window title (partial match ok). Use this "
                    "before click_control or type_into_control to see what's actually there.",
    "parameters": {"type": "object", "properties": {
        "window_title": {"type": "string", "description": "the window's title, or part of it"}},
        "required": ["window_title"]}})
def list_controls(window_title: str):
    win = _find_window(window_title)
    if not win:
        return f"No open window matching '{window_title}'."
    _activate(win)
    # Hyperlink matters most on web pages - a typical site is mostly links, and
    # leaving it out made every page look like it had nothing to click.
    wanted = {"Button", "CheckBox", "RadioButton", "Edit", "ComboBox",
              "TabItem", "MenuItem", "ListItem", "Hyperlink", "TreeItem"}
    seen, out = set(), []
    try:
        for c in win.descendants():
            ctype = c.element_info.control_type
            name = (c.window_text() or "").strip()
            if ctype not in wanted or not name:
                continue
            key = (ctype, name)
            if key in seen:
                continue
            seen.add(key)
            out.append(f"[{ctype}] {name}")
            if len(out) >= 40:
                break
    except Exception as e:
        return f"Couldn't read controls: {e}"
    if not out:
        return f"No labeled controls found in '{win.window_text()}'."
    return f"Controls in '{win.window_text()}':\n" + "\n".join(out)

@register({"name": "click_control",
    "description": "Click a button or control by name inside a specific open window. "
                    "Use list_controls first to see exact names. Actions that look "
                    "dangerous (payment, delete, submit, etc.) require the user to "
                    "confirm out loud before Ghost proceeds.",
    "parameters": {"type": "object", "properties": {
        "window_title": {"type": "string"},
        "control_name": {"type": "string", "description": "exact or partial control label to click"}},
        "required": ["window_title", "control_name"]}})
def click_control(window_title: str, control_name: str):
    win = _find_window(window_title)
    if not win:
        return f"No open window matching '{window_title}'."
    query = control_name.lower().strip()
    clickable = {"Button", "CheckBox", "RadioButton", "TabItem", "MenuItem",
                 "ListItem", "Hyperlink", "TreeItem", "ComboBox"}

    def _visible(c):
        try:
            if not c.is_visible():
                return False
            r = c.rectangle()
            # Off-screen accessibility helpers ("skip to content" links) sit at
            # large negative coordinates; clicking those fires at a junk position.
            return r.right > 0 and r.bottom > 0 and r.width() > 0 and r.height() > 0
        except Exception:
            return False

    hits = []
    try:
        for c in win.descendants():
            if c.element_info.control_type not in clickable:
                continue
            name = (c.window_text() or "").strip()
            if name and query in name.lower():
                hits.append((c, name))
    except Exception as e:
        return f"Couldn't search controls: {e}"
    if not hits:
        return f"No clickable control named '{control_name}' found in '{win.window_text()}'."

    # Visible always beats invisible, then exact name over partial. Matching an
    # exact-but-hidden element (a collapsed menu item, or a link below the fold)
    # and clicking its stale coordinates is how this silently did nothing.
    visible_hits = [(c, n) for c, n in hits if _visible(c)]
    if not visible_hits:
        names = ", ".join(sorted({n for _c, n in hits})[:4])
        return (f"Found '{control_name}' in '{win.window_text()}' but it isn't "
                f"visible on screen right now (matched: {names}). It may be below "
                "the fold or inside a menu that needs opening first.")
    target, _ = next(((c, n) for c, n in visible_hits if n.lower() == query),
                     visible_hits[0])

    label = target.window_text()
    if any(word in label.lower() for word in DANGEROUS_UI):
        print(f"\n⚠️  Ghost wants to click '{label}' in '{win.window_text()}'")
        ok = input("Type yes to allow: ")
        if ok.strip().lower() != "yes":
            return f"User denied clicking '{label}'."
    try:
        win.set_focus()
        target.click_input()
        return f"Clicked '{label}'."
    except Exception as e:
        return f"Found '{label}' but couldn't click it: {e}"

@register({"name": "type_into_control",
    "description": "Type text into a text field/control inside a specific open window. "
                    "Use list_controls first to find the field's name.",
    "parameters": {"type": "object", "properties": {
        "window_title": {"type": "string"},
        "control_name": {"type": "string"},
        "text": {"type": "string"}},
        "required": ["window_title", "control_name", "text"]}})
def type_into_control(window_title: str, control_name: str, text: str):
    win = _find_window(window_title)
    if not win:
        return f"No open window matching '{window_title}'."
    query = control_name.lower().strip()
    editable = ("Edit", "ComboBox", "Document")
    target = None
    try:
        for c in win.descendants():
            name = (c.window_text() or "").strip()
            if c.element_info.control_type in editable and query in name.lower():
                target = c
                break
        if target is None:
            for c in win.descendants():
                if c.element_info.control_type in editable:
                    target = c
                    break
    except Exception as e:
        return f"Couldn't search controls: {e}"
    if target is None:
        return f"No text field named '{control_name}' found in '{win.window_text()}'."
    try:
        win.set_focus()
        target.set_focus()
        _set_clipboard(text)
        target.type_keys('^a^v')  # select-all then paste, replacing any existing content
        return f"Typed into '{control_name}'."
    except Exception as e:
        return f"Found the field but couldn't type into it: {e}"

@register({"name": "scroll_window",
    "description": "Scroll a window or web page. Use when something the user asked "
                    "for isn't visible yet - click_control reports when a target is "
                    "below the fold, and scrolling then brings it into view.",
    "parameters": {"type": "object", "properties": {
        "window_title": {"type": "string"},
        "direction": {"type": "string",
                       "description": "down, up, top or bottom. Default down."},
        "amount": {"type": "integer",
                    "description": "how many pages to scroll, default 1"}},
        "required": ["window_title"]}})
def scroll_window(window_title: str, direction: str = "down", amount: int = 1):
    win = _find_window(window_title)
    if not win:
        return f"No open window matching '{window_title}'."
    _activate(win, settle=0.4)
    d = (direction or "down").lower().strip()
    if d not in ("down", "up", "top", "bottom"):
        return f"Direction must be down, up, top or bottom - not '{direction}'."
    # Mouse wheel over the page body, NOT PageDown/End. type_keys goes to whatever
    # holds keyboard focus, so with the cursor in the address bar those keys
    # navigate or edit text instead of scrolling - that actually changed pages
    # mid-test rather than scrolling them.
    from pywinauto import mouse
    try:
        r = win.rectangle()
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        steps = {"top": 25, "bottom": 25}.get(d, max(1, min(int(amount or 1), 15)))
        sign = 1 if d in ("up", "top") else -1
        for _ in range(steps):
            mouse.scroll(coords=(cx, cy), wheel_dist=sign * 5)
            time.sleep(0.12)
        time.sleep(0.6)   # let the page settle before anything reads it
    except Exception as e:
        return f"Couldn't scroll that window: {e}"
    if d in ("top", "bottom"):
        return f"Scrolled to the {d} of the page."
    return f"Scrolled {d} {steps} step(s)."

@register({"name": "read_window_text",
    "description": "Read the visible text content of a specific open window, e.g. to "
                    "summarize a dialog, article, or screen state to the user.",
    "parameters": {"type": "object", "properties": {
        "window_title": {"type": "string"}}, "required": ["window_title"]}})
def read_window_text(window_title: str):
    win = _find_window(window_title)
    if not win:
        return f"No open window matching '{window_title}'."
    _activate(win)
    chunks, seen = [], set()
    try:
        for c in win.descendants():
            text = (c.window_text() or "").strip()
            if text and text not in seen:
                seen.add(text)
                chunks.append(text)
    except Exception as e:
        return f"Couldn't read window text: {e}"
    return " | ".join(chunks)[:3000] or "No readable text found in that window."
