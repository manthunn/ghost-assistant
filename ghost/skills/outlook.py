"""Read the user's mail out of the new Outlook (olk.exe) via UI Automation.

New Outlook dropped COM/VBA support, so there's no Outlook.Application object to
talk to. It is a WebView2 app though, and once its window has focus it exposes a
full UIA accessibility tree: the message list shows up as ListItem controls, and
the open email's body as a Document that contains no ListItems. That's enough to
read mail without any Azure app registration or OAuth setup.

Read-only by design - sending mail is a separate, confirmation-gated concern.
"""
import re
import time
from . import register
from .ui_automation import _find_window, _activate

# Marketing mail pads previews with invisible filler (combining grapheme joiner,
# zero-width spaces, soft hyphens, bidi marks). Strip it - it's pure token bloat
# and makes spoken summaries read badly.
_INVISIBLE = re.compile(
    "[­͏​‌‍‎‏"
    "‪‫‬‭‮⁠﻿]")

def _clean(s, limit=None):
    out = " ".join(_INVISIBLE.sub("", s or "").split())
    return out[:limit] if limit else out

def _outlook_window():
    win = _find_window("Outlook")
    return _activate(win) if win else None

def _list_items(win):
    return [c for c in win.descendants()
            if c.element_info.control_type == "ListItem"]

def _reading_pane(win):
    """The Document holding the open email - i.e. the one with no ListItems in it
    (the other Document is the whole app shell, message list included)."""
    best, best_n = None, -1
    for d in win.descendants():
        if d.element_info.control_type != "Document":
            continue
        kids = d.descendants()
        if any(k.element_info.control_type == "ListItem" for k in kids):
            continue  # that's the app shell, not the reading pane
        if len(kids) > best_n:
            best, best_n = d, len(kids)
    return best

def _text_of(element, limit=4000):
    seen, chunks = set(), []
    for c in element.descendants():
        t = _clean(c.window_text())
        if t and t not in seen:
            seen.add(t)
            chunks.append(t)
    return " | ".join(chunks)[:limit]

@register({"name": "read_inbox",
    "description": "List the most recent emails in the user's Outlook inbox "
                    "(sender, subject, preview, time). Outlook must be open. Use "
                    "this to answer 'any new email?' or before read_email.",
    "parameters": {"type": "object", "properties": {
        "count": {"type": "integer", "description": "how many recent emails, default 5"}},
        "required": []}})
def read_inbox(count: int = 5):
    win = _outlook_window()
    if not win:
        return "Outlook doesn't appear to be open - open it with open_app first."
    items = _list_items(win)
    if not items:
        return "Outlook is open but the message list couldn't be read."
    count = max(1, min(int(count or 5), 15))
    out = []
    for c in items[:count]:
        t = _clean(c.window_text(), 220)
        if t:
            out.append(f"- {t}")
    if not out:
        return "The message list appears to be empty."
    return f"{len(out)} most recent inbox messages:\n" + "\n".join(out)

@register({"name": "read_email",
    "description": "Open a specific email in Outlook and read its full body. Match "
                    "it by any text from its sender or subject (e.g. 'LinkedIn' or "
                    "'car insurance'). Opening an email marks it as read.",
    "parameters": {"type": "object", "properties": {
        "match": {"type": "string",
                   "description": "text from the sender or subject to identify the email"}},
        "required": ["match"]}})
def read_email(match: str):
    win = _outlook_window()
    if not win:
        return "Outlook doesn't appear to be open - open it with open_app first."
    query = _clean(match).lower()
    target = None
    for c in _list_items(win):
        if query and query in _clean(c.window_text()).lower():
            target = c
            break
    if target is None:
        return (f"No email matching '{match}' in the visible inbox. "
                "Try read_inbox first to see what's there.")
    header = _clean(target.window_text(), 200)
    try:
        target.click_input()
    except Exception as e:
        return f"Found the email but couldn't open it: {e}"
    time.sleep(2)  # let the reading pane load
    pane = _reading_pane(win)
    if pane is None:
        return f"Opened '{header}' but couldn't read the reading pane."
    body = _text_of(pane)
    if not body:
        return f"Opened '{header}' but its body appears empty."
    return f"Email: {header}\n\nBody:\n{body}"
