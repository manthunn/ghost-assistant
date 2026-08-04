"""Read the Claude and ChatGPT desktop apps' conversation lists.

Both are Electron/WebView2 apps, so the same UI Automation path that reads new
Outlook works here: focus the window, walk its accessibility tree. That is the
whole trick - asked "did I ever talk to Claude about my BCI project", Ghost
used to scroll whatever was on screen, because nothing told it the app exposes
its own sidebar as readable structure.

Not the browser. Manny uses the desktop apps, and an anonymous HTTP fetch of
claude.ai only ever returns a login page - see browser.read_webpage.

Two app-specific shapes, both derived by dumping the real trees:

  ChatGPT  projects are Text rows; each expanded project owns a List named
           "Chats in <project>" whose ListItems are "<title><relative age>",
           e.g. 'BCI Drone Project Setup1w'.
  Claude   sessions are Buttons, each paired with a sibling Button named
           "More options for <title>". That pairing is what separates a real
           conversation from ordinary UI chrome.

A COLLAPSED ChatGPT project exposes no chat items at all - the same
virtualisation trap that hid the Monash mailbox in outlook.py. Collapsed
projects are reported rather than silently treated as empty, so Ghost never
says "you have no BCI chats" when it simply could not see them.
"""
import re
import time
from . import register
from .ui_automation import _find_window, _activate

APPS = {
    "claude": "Claude",
    "chatgpt": "ChatGPT",
}

# ListItem names carry a trailing relative age with no separator:
# 'Phase 1 Commit Setup6d', 'BCI project analysis10mo'.
_AGE_SUFFIX = re.compile(r"(\d+)\s*(s|m|h|d|w|mo|y)$", re.I)


def _clean(s):
    return " ".join((s or "").split())


def _split_age(name):
    """('BCI Drone Project Setup', '1w') - the age is glued onto the title."""
    name = _clean(name)
    m = _AGE_SUFFIX.search(name)
    if not m:
        return name, ""
    return name[:m.start()].strip(), m.group(0)


def _safe_walk(win):
    """[(control_type, name, element)], tolerating a tree that mutates mid-walk.

    A live Electron app reshuffles elements while they are being read; a plain
    list comprehension over descendants() dies partway with a COMError.
    """
    rows = []
    try:
        kids = win.descendants()
    except Exception:
        return rows
    for c in kids:
        try:
            rows.append((c.element_info.control_type, _clean(c.window_text()), c))
        except Exception:
            continue
    return rows


def _chatgpt_chats(rows):
    """({project: [(title, age)]}, [collapsed_project_names])."""
    lists = {}
    for ctype, name, el in rows:
        if ctype != "List" or not name.lower().startswith("chats in "):
            continue
        project = name[len("chats in "):].strip()
        items = []
        try:
            kids = el.descendants()
        except Exception:
            kids = []
        for k in kids:
            try:
                if k.element_info.control_type != "ListItem":
                    continue
                title, age = _split_age(k.window_text())
            except Exception:
                continue
            if title:
                items.append((title, age))
        lists[project] = items

    # Every project row, so a project with no visible List can be reported as
    # collapsed rather than as genuinely empty.
    projects = []
    for i, (ctype, name, _el) in enumerate(rows):
        if ctype == "Button" and name.lower().startswith("project actions for "):
            projects.append(name[len("project actions for "):].strip())
    collapsed = [p for p in projects if p not in lists]
    return lists, collapsed


def _claude_chats(rows):
    """[(title, '')] - Claude sessions, identified by their 'More options' twin."""
    titles = []
    for ctype, name, _el in rows:
        if ctype != "Button":
            continue
        low = name.lower()
        if low.startswith("more options for "):
            t = name[len("more options for "):].strip()
            if t and t not in titles:
                titles.append(t)
    return [(t, "") for t in titles]


def _read_app(app):
    """(display_name, {group: [(title, age)]}, collapsed, error_or_None)."""
    key = (app or "").strip().lower()
    for k in APPS:
        if k in key or key in k:
            key = k
            break
    else:
        return None, {}, [], (f"Unknown app '{app}'. Known: "
                              f"{', '.join(sorted(APPS))}.")
    title = APPS[key]
    win = _find_window(title)
    if win is None:
        return title, {}, [], (f"The {title} desktop app doesn't appear to be "
                                f"running. Open it first, then ask again.")
    # Electron/WebView2 apps report empty chrome until focused.
    _activate(win, settle=2.0)
    rows = _safe_walk(win)
    if not rows:
        return title, {}, [], f"Couldn't read the {title} window."
    if key == "chatgpt":
        groups, collapsed = _chatgpt_chats(rows)
    else:
        groups, collapsed = {"": _claude_chats(rows)}, []
    return title, groups, collapsed, None


def _format(title, groups, collapsed, query=""):
    q = _clean(query).lower()
    lines, hits = [], 0
    for group, items in groups.items():
        keep = [(t, a) for t, a in items if not q or q in t.lower()]
        if not keep:
            continue
        hits += len(keep)
        lines.append(f"{group}:" if group else "Conversations:")
        for t, a in keep:
            lines.append(f"  - {t}" + (f" ({a} ago)" if a else ""))
    head = (f"{hits} conversation(s) in {title} matching '{query}'."
            if q else f"{hits} conversation(s) visible in {title}.")
    out = [head] + lines
    if collapsed:
        # Never let a collapsed group read as "nothing there".
        out.append(f"\nNOT SEARCHED - these {title} projects are collapsed, so "
                   f"their chats aren't readable: {', '.join(collapsed)}. Say so "
                   f"rather than concluding the user has no such conversation.")
    if not hits and q and not collapsed:
        out.append(f"\nNothing matching '{query}' among the visible conversations.")
    return "\n".join(out)


@register({"name": "search_ai_chats",
    "description": "Search or list the user's past conversations in his Claude or "
                    "ChatGPT DESKTOP app, by reading the app's own sidebar. Use this "
                    "for questions like 'did I talk to ChatGPT about my BCI project', "
                    "'what Claude chats do I have about X', 'find my conversation "
                    "about Y'. This reads the real conversation list - never scroll "
                    "or screenshot the window to answer these.",
    "parameters": {"type": "object", "properties": {
        "app": {"type": "string",
                 "description": "'claude' or 'chatgpt'"},
        "query": {"type": "string",
                   "description": "text to match in conversation titles. Omit to "
                                  "list everything visible."}},
        "required": ["app"]}})
def search_ai_chats(app: str, query: str = ""):
    title, groups, collapsed, err = _read_app(app)
    if err:
        return err
    return _format(title, groups, collapsed, query)


@register({"name": "new_ai_chat",
    "description": "Start a new chat in the user's Claude or ChatGPT desktop app. "
                    "Use when he asks to open a new chat / start a fresh "
                    "conversation with one of them.",
    "parameters": {"type": "object", "properties": {
        "app": {"type": "string", "description": "'claude' or 'chatgpt'"}},
        "required": ["app"]}})
def new_ai_chat(app: str):
    key = (app or "").strip().lower()
    match = next((k for k in APPS if k in key or key in k), None)
    if match is None:
        return f"Unknown app '{app}'. Known: {', '.join(sorted(APPS))}."
    title = APPS[match]
    win = _find_window(title)
    if win is None:
        return (f"The {title} desktop app doesn't appear to be running. "
                f"Open it first.")
    _activate(win, settle=1.5)
    # Ctrl+N rather than hunting for a button: both apps use it, and the visible
    # "Create"/"New" buttons differ between them and between versions.
    try:
        from pywinauto.keyboard import send_keys
        send_keys("^n")
    except Exception as e:
        return f"Couldn't send the new-chat shortcut to {title}: {e}"
    time.sleep(1.0)
    return (f"Sent Ctrl+N to {title} to start a new chat, and brought it to the "
            f"front. Tell the user it's ready for them to type in.")
