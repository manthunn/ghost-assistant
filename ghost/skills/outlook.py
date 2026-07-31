"""Read the user's mail out of the new Outlook (olk.exe) via UI Automation.

New Outlook dropped COM/VBA support, so there's no Outlook.Application object to
talk to. It is a WebView2 app though, and once its window has focus it exposes a
full UIA accessibility tree: the message list shows up as ListItem controls, and
the open email's body as a Document that contains no ListItems. That's enough to
read mail without any Azure app registration or OAuth setup.

Read-only by design - sending mail is a separate, confirmation-gated concern.
"""
import os
import re
import time
import urllib.parse
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

def _window_titles():
    from .ui_automation import _desktop
    out = set()
    try:
        for w in _desktop().windows():
            t = (w.window_text() or "").strip()
            if t:
                out.add(t)
    except Exception:
        pass
    return out

def _outlook_window(launch_if_missing=True, wait_secs=25):
    """Find the Outlook window, starting Outlook if it isn't running.

    Note this must reach NEW Outlook (olk.exe). "outlook" on PATH resolves to
    classic Outlook, which has no account configured here - open_app's alias
    handles that.
    """
    win = _find_window("Outlook")
    if win:
        return _activate(win)
    if not launch_if_missing:
        return None
    from .system_control import open_app
    open_app("outlook")
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        time.sleep(2)
        win = _find_window("Outlook")
        if win:
            return _activate(win)
    return None

def _list_items(win, wait_secs=18):
    """Message rows, waiting for them to render.

    A freshly-launched Outlook reports its window immediately but takes several
    seconds to populate the message list, so reading straight away returns
    nothing and looks like a failure. Poll instead of giving up on the first try.
    """
    deadline = time.time() + wait_secs
    while True:
        items = [c for c in win.descendants()
                 if c.element_info.control_type == "ListItem"]
        if items or time.time() >= deadline:
            return items
        time.sleep(1.5)

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

MAILTO_LIMIT = 1800  # long mailto URLs get truncated by Windows/Outlook

@register({"name": "compose_email",
    "description": "Write an email and open it in Outlook, pre-filled and ready for the "
                    "user to review and send. Use when the user wants to email someone. "
                    "Ghost does not send it - the user reads it over and clicks Send "
                    "themselves, so say so after composing.",
    "parameters": {"type": "object", "properties": {
        "to": {"type": "string", "description": "recipient email address(es), comma separated"},
        "subject": {"type": "string"},
        "body": {"type": "string", "description": "the full message text"},
        "cc": {"type": "string", "description": "optional cc address(es)"}},
        "required": ["to", "subject", "body"]}})
def compose_email(to: str, subject: str, body: str, cc: str = ""):
    to = _clean(to)
    if "@" not in to:
        return f"'{to}' doesn't look like an email address - I need a real recipient."
    params = {"subject": subject or "", "body": body or ""}
    if cc.strip():
        params["cc"] = _clean(cc)
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    url = f"mailto:{urllib.parse.quote(to, safe='@,;')}?{query}"
    if len(url) > MAILTO_LIMIT:
        return (f"That message is too long to hand to Outlook this way "
                f"({len(url)} chars, limit ~{MAILTO_LIMIT}). Ask me to shorten it.")
    # startfile succeeding only means the shell accepted the URL - it says nothing
    # about a draft window actually appearing. Snapshot windows and confirm.
    before = _window_titles()
    try:
        os.startfile(url)
    except OSError as e:
        return f"Couldn't open a draft in Outlook: {e}"
    appeared = None
    for _ in range(12):
        time.sleep(0.5)
        new = _window_titles() - before
        appeared = next((t for t in new if subject[:24].lower() in t.lower()
                          or "outlook" in t.lower()), None)
        if appeared:
            break
    preview = " ".join((body or "").split())[:90]
    tail = (f"It is NOT sent - review it and press Send.")
    if appeared:
        return (f"Draft open in Outlook to {to}, subject '{subject}'. "
                f"Body starts: \"{preview}...\". {tail}")
    return (f"Handed the draft to Outlook for {to}, subject '{subject}', but couldn't "
            f"confirm a compose window opened - it may be behind another window, or "
            f"Outlook may still be starting. Tell the user to check Outlook. {tail}")

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
