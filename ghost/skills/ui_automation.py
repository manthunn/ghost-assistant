import time
import win32clipboard
from pywinauto import Desktop
from . import register

def _set_clipboard(text):
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
    wanted = {"Button", "CheckBox", "RadioButton", "Edit", "ComboBox",
              "TabItem", "MenuItem", "ListItem"}
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
    clickable = {"Button", "CheckBox", "RadioButton", "TabItem", "MenuItem", "ListItem"}
    target = None
    try:
        for c in win.descendants():
            if c.element_info.control_type not in clickable:
                continue
            name = (c.window_text() or "").strip()
            if name and query in name.lower():
                target = c
                if name.lower() == query:
                    break
    except Exception as e:
        return f"Couldn't search controls: {e}"
    if target is None:
        return f"No clickable control named '{control_name}' found in '{win.window_text()}'."

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
