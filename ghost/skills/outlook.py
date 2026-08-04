"""Read the user's mail out of the new Outlook (olk.exe) via UI Automation.

New Outlook dropped COM/VBA support, so there's no Outlook.Application object to
talk to. It is a WebView2 app though, and once its window has focus it exposes a
full UIA accessibility tree: the message list shows up as ListItem controls, and
the open email's body as a Document that contains no ListItems. That's enough to
read mail without any Azure app registration or OAuth setup.

Read-only by design - sending mail is a separate, confirmation-gated concern.

Outlook is left exactly as it was found: if Ghost had to launch it to read
something, Ghost closes it again. If it was already open it is left alone -
closing a window the user was working in would be worse than leaving one open.
"""
import os
import re
import time
import subprocess
import urllib.parse
from contextlib import contextmanager
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

def _launch_outlook(wait_secs=25):
    """Start Outlook and wait for its window. Returns the window, or None.

    Note this must reach NEW Outlook (olk.exe). "outlook" on PATH resolves to
    classic Outlook, which has no account configured here - open_app's alias
    handles that.
    """
    from .system_control import open_app
    open_app("outlook")
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        time.sleep(2)
        win = _find_window("Outlook")
        if win:
            return _activate(win)
    return None


def _close_outlook(win):
    """Close the Outlook window Ghost opened. True if it actually went away."""
    try:
        win.close()
    except Exception:
        pass
    for _ in range(8):
        time.sleep(0.5)
        if _find_window("Outlook") is None:
            return True
    # WebView2 apps occasionally ignore WM_CLOSE while still starting up. Ask the
    # process to exit - no /F, so it still shuts down cleanly and any draft gets
    # its normal save prompt rather than being destroyed.
    try:
        subprocess.run(["taskkill", "/IM", "olk.exe"],
                       capture_output=True, timeout=10)
    except Exception:
        return False
    time.sleep(1.5)
    return _find_window("Outlook") is None


# depth: how many nested outlook_session() blocks are active. launched: whether
# *Ghost* started Outlook this session (only then may it close it).
_session = {"depth": 0, "launched": False, "win": None}


@contextmanager
def outlook_session():
    """Open Outlook if needed, and close it afterwards only if Ghost opened it.

    Reentrant on purpose. The briefing calls check_mail() and read_inbox() back
    to back; without nesting that would launch Outlook, close it, and launch it
    again - two ~25 second waits for one briefing section. Only the outermost
    block closes.

    Yields the window, or None if Outlook couldn't be opened at all.
    """
    _session["depth"] += 1
    try:
        if _session["win"] is None:
            existing = _find_window("Outlook")
            if existing is not None:
                # Already open - the user may be using it. Leave it open after.
                _session["win"] = _activate(existing)
                _session["launched"] = False
            else:
                win = _launch_outlook()
                _session["win"] = win
                _session["launched"] = win is not None
        yield _session["win"]
    finally:
        _session["depth"] -= 1
        if _session["depth"] <= 0:
            win, launched = _session["win"], _session["launched"]
            _session.update({"depth": 0, "launched": False, "win": None})
            if launched and win is not None:
                _close_outlook(win)

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

def _accounts(win):
    """[(email, [(folder_label, element), ...]), ...] read off the folder tree.

    Account rows are TreeItems whose text is a bare address; every folder row
    after one belongs to it until the next account or the 'Add account' row.
    """
    out, current = [], None
    for c in win.descendants():
        if c.element_info.control_type != "TreeItem":
            continue
        label = _clean(c.window_text())
        if not label:
            continue
        if label.lower().startswith("add account"):
            break
        if "@" in label and " " not in label:
            current = (label, [])
            out.append(current)
        elif current is not None:
            current[1].append((label, c))
    return out

def _inbox_of(folders):
    for label, el in folders:
        if label.lower().startswith("inbox"):
            return label, el
    return None, None

def _unread_count(label):
    m = re.search(r"(\d+)\s+unread", label or "")
    return int(m.group(1)) if m else 0

@register({"name": "check_mail",
    "description": "Report unread email counts across all of the user's Outlook "
                    "accounts (personal and university). Fast - reads the folder "
                    "list without opening any message. Use for 'any new mail?', "
                    "'do I have unread emails', or as part of a morning briefing.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def check_mail():
    with outlook_session() as win:
        if not win:
            return "Couldn't open Outlook to check mail."
        accounts = _accounts(win)
        if not accounts:
            return "Outlook is open but its account list couldn't be read."
        lines, total = [], 0
        for email, folders in accounts:
            label, _el = _inbox_of(folders)
            if label is None:
                lines.append(f"{email}: no inbox found")
                continue
            n = _unread_count(label)
            total += n
            lines.append(f"{email}: {n} unread" if n else f"{email}: nothing new")
        head = f"{total} unread across {len(accounts)} account(s)."
        return head + "\n" + "\n".join(f"- {l}" for l in lines)

_ACCOUNT_HINTS = {
    "monash": ("monash", "student"), "university": ("monash", "student"),
    "uni": ("monash", "student"), "student": ("monash", "student"),
    "school": ("monash", "student"),
    "personal": ("outlook.com", "hotmail", "live."),
    "private": ("outlook.com", "hotmail", "live."),
}

def _switch_account(win, account):
    """Click the named account's Inbox. True on success, else a message to relay."""
    accounts = _accounts(win)
    if not accounts:
        return "Outlook is open but its account list couldn't be read."
    q = _clean(account).lower()
    needles = _ACCOUNT_HINTS.get(q, (q,))
    match = next((a for a in accounts
                  if any(n in a[0].lower() for n in needles)), None)
    if match is None:
        have = ", ".join(a[0] for a in accounts)
        return f"No Outlook account matching '{account}'. Accounts available: {have}."
    label, el = _inbox_of(match[1])
    if el is None:
        return f"Couldn't find an Inbox under {match[0]}."
    try:
        el.click_input()
        time.sleep(2.5)   # let the message list repopulate for the new account
    except Exception as e:
        return f"Couldn't switch to {match[0]}: {e}"
    return True

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
    "description": "List the most recent emails in an Outlook inbox (sender, "
                    "subject, preview, time). The user has two accounts - a personal "
                    "one and his Monash university one - so pass 'account' to pick, "
                    "e.g. 'monash', 'student', or 'personal'. Opens Outlook if it "
                    "isn't running. Use check_mail first for a quick unread count.",
    "parameters": {"type": "object", "properties": {
        "count": {"type": "integer", "description": "how many recent emails, default 5"},
        "account": {"type": "string",
                     "description": "which mailbox: part of the address, or "
                                    "'monash'/'university'/'personal'. Omit for the "
                                    "one currently open."}},
        "required": []}})
def read_inbox(count: int = 5, account: str = ""):
    with outlook_session() as win:
        if not win:
            return "Couldn't open Outlook to read the inbox."
        if account.strip():
            switched = _switch_account(win, account)
            if switched is not True:
                return switched          # an explanatory message
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
    with outlook_session() as win:
        if not win:
            return "Couldn't open Outlook to read that email."
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
