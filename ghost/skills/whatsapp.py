"""Read and send WhatsApp from the desktop app.

The app is a web view wrapped in a native window, and its window carries about
42,000 UIA elements. That number drives the whole design here: a plain
descendants() search for the chat list takes ~8 seconds, which is far too slow
to run on every question.

The tree is narrow and deep rather than wide - roughly 2 to 6 nodes per level
down to depth 16, where the actual UI starts, with the chat list around depth
18. So a breadth-first walk by children() finds anything in well under a
second, while descendants() pays for the entire subtree. _find is that walk.

Sending a message and placing a call are gated. Both are irreversible and
visible to somebody else the instant they fire - an accidental call at 3am is
not undoable - so the tool refuses unless the caller passes confirm=True, and
the description tells Ghost to read the recipient and message back first and
wait for an explicit yes.
"""
import re
import time

from . import register
from .ui_automation import _find_window, _activate

WINDOW = "WhatsApp"
APP_ID = r"5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
MAX_DEPTH = 24

# Chat rows read as "<name> <timestamp> <preview>". Splitting on the timestamp
# is what separates a contact's name from the message text.
_STAMP = re.compile(
    r"\s(?=(?:\d{1,2}:\d{2}(?:\s?[APap][Mm])?|Yesterday|Today|"
    r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"\d{1,2}/\d{1,2}/\d{2,4})\b)")


def _clean(s):
    return " ".join((s or "").split())


def _launch():
    import subprocess
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{APP_ID}"],
                     shell=False)


def _window(wait_secs=25):
    win = _find_window(WINDOW)
    if win is None:
        _launch()
        deadline = time.time() + wait_secs
        while time.time() < deadline:
            time.sleep(2)
            win = _find_window(WINDOW)
            if win:
                break
    if win is None:
        return None
    return _activate(win, settle=2.0)


def _find(win, match, depth=MAX_DEPTH, first=True):
    """Breadth-first search by children(). Returns element(s) matching `match`.

    match(control_type, name) -> bool. Used instead of descendants() because
    descendants() walks all ~42k elements; this walks the ~250 that lie on the
    path to the visible UI.
    """
    found = []
    level, d = [win], 0
    while level and d < depth:
        nxt = []
        for node in level:
            try:
                kids = node.children()
            except Exception:
                continue
            for k in kids:
                try:
                    ct = k.element_info.control_type
                    nm = _clean(k.window_text())
                except Exception:
                    continue
                if match(ct, nm):
                    if first:
                        return k
                    found.append(k)
                nxt.append(k)
        level, d = nxt, d + 1
    return None if first else found


def _chat_list(win):
    return _find(win, lambda ct, nm: ct == "DataGrid" and nm.lower() == "chat list")


def _parse_row(text):
    """'Riks 20:14 Sorry need to use the loo' -> ('Riks', '20:14', 'Sorry...')."""
    text = _clean(text)
    parts = _STAMP.split(text, maxsplit=1)
    if len(parts) < 2:
        return text, "", ""
    name, rest = parts[0].strip(), parts[1].strip()
    bits = rest.split(" ", 1)
    when = bits[0]
    if len(bits) > 1 and re.fullmatch(r"[APap][Mm]", bits[1][:2] or ""):
        when += " " + bits[1][:2]
        preview = bits[1][2:].strip()
    else:
        preview = bits[1].strip() if len(bits) > 1 else ""
    return name, when, preview


def _rows(win, limit=20):
    grid = _chat_list(win)
    if grid is None:
        return None
    out, seen = [], set()
    try:
        kids = grid.children()
    except Exception:
        return None
    for c in kids:
        try:
            if c.element_info.control_type != "DataItem":
                continue
            name, when, preview = _parse_row(c.window_text())
        except Exception:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "when": when, "preview": preview, "el": c})
        if len(out) >= limit:
            break
    return out


@register({"name": "check_whatsapp",
    "description": "See who has messaged the user on WhatsApp, with the latest "
                    "message from each chat. Use for 'who messaged me', 'any "
                    "WhatsApp messages', 'what did X say'. Opens WhatsApp if it "
                    "isn't running. Read-only.",
    "parameters": {"type": "object", "properties": {
        "count": {"type": "integer", "description": "how many chats, default 8"}},
        "required": []}})
def check_whatsapp(count: int = 8):
    win = _window()
    if win is None:
        return "Couldn't open WhatsApp."
    rows = _rows(win, limit=max(1, min(int(count or 8), 20)))
    if rows is None:
        return ("WhatsApp is open but its chat list couldn't be read - it may still "
                "be loading, or be showing a different screen.")
    if not rows:
        return "WhatsApp's chat list is empty."
    lines = [f"- {r['name']}" + (f" ({r['when']})" if r["when"] else "")
             + (f": {r['preview']}" if r["preview"] else "")
             for r in rows]
    return (f"{len(lines)} most recent WhatsApp chats:\n" + "\n".join(lines)
            + "\n\nSummarise who messaged and roughly what about; don't read every "
              "line out verbatim.")


def _open_chat(win, contact):
    """Search for a contact and open their chat. True, or a message to relay."""
    box = _find(win, lambda ct, nm: ct == "Edit" and "search" in nm.lower())
    if box is None:
        return "Couldn't find WhatsApp's search box."
    try:
        box.set_focus()
        # Clear anything already typed, or the search stacks up.
        from pywinauto.keyboard import send_keys
        send_keys("^a{BACKSPACE}")
        time.sleep(0.3)
        box.type_keys(contact, with_spaces=True, pause=0.02)
        time.sleep(1.8)          # let results filter
        send_keys("{ENTER}")
        time.sleep(1.5)          # let the conversation open
    except Exception as e:
        return f"Couldn't search for '{contact}': {e}"
    return True


@register({"name": "send_whatsapp",
    "description": "Send a WhatsApp message to a contact. THIS SENDS IMMEDIATELY "
                    "and cannot be undone. Before calling it, read the recipient's "
                    "name and the exact message back to the user and get a clear "
                    "spoken yes; only then call it with confirm=true. Never pass "
                    "confirm=true on your own initiative or because the user seemed "
                    "to want it - the user must actually approve that message text.",
    "parameters": {"type": "object", "properties": {
        "contact": {"type": "string", "description": "contact or group name"},
        "message": {"type": "string", "description": "exact text to send"},
        "confirm": {"type": "boolean",
                     "description": "true only after the user has heard the "
                                    "recipient and message and said yes"}},
        "required": ["contact", "message"]}})
def send_whatsapp(contact: str, message: str, confirm: bool = False):
    contact, message = _clean(contact), (message or "").strip()
    if not contact or not message:
        return "Need both a contact and a message."
    if not confirm:
        return (f"NOT SENT - needs confirmation. Read this back to the user and ask "
                f"if it should go: to {contact}, \"{message}\". If they say yes, "
                f"call send_whatsapp again with confirm=true.")

    win = _window()
    if win is None:
        return "Couldn't open WhatsApp."
    opened = _open_chat(win, contact)
    if opened is not True:
        return opened
    try:
        from pywinauto.keyboard import send_keys
        box = _find(win, lambda ct, nm: ct == "Edit" and "type a message" in nm.lower())
        if box is not None:
            box.set_focus()
        box_ok = box is not None
        send_keys(message.replace("\n", " "), with_spaces=True, pause=0.01)
        time.sleep(0.4)
        send_keys("{ENTER}")
        time.sleep(0.8)
    except Exception as e:
        return f"Opened the chat but couldn't send: {e}"
    note = "" if box_ok else (" Note: the message box wasn't positively identified, "
                              "so ask the user to confirm it actually sent.")
    return f"Sent to {contact}: \"{message}\".{note}"


@register({"name": "call_whatsapp",
    "description": "Start a WhatsApp voice or video call with a contact. THIS RINGS "
                    "THEIR PHONE IMMEDIATELY and cannot be undone. Confirm who and "
                    "which kind of call with the user out loud first, then call with "
                    "confirm=true. Never pass confirm=true unprompted.",
    "parameters": {"type": "object", "properties": {
        "contact": {"type": "string", "description": "contact name"},
        "kind": {"type": "string", "enum": ["voice", "video"],
                  "description": "'voice' or 'video', default voice"},
        "confirm": {"type": "boolean",
                     "description": "true only after the user explicitly approved"}},
        "required": ["contact"]}})
def call_whatsapp(contact: str, kind: str = "voice", confirm: bool = False):
    contact = _clean(contact)
    kind = (kind or "voice").strip().lower()
    if kind not in ("voice", "video"):
        kind = "voice"
    if not contact:
        return "Who should I call?"
    if not confirm:
        return (f"NOT CALLED - needs confirmation. Ask the user to confirm a {kind} "
                f"call to {contact}, then call again with confirm=true.")

    win = _window()
    if win is None:
        return "Couldn't open WhatsApp."
    opened = _open_chat(win, contact)
    if opened is not True:
        return opened

    want = "video call" if kind == "video" else "voice call"
    btn = _find(win, lambda ct, nm: ct == "Button" and nm.lower() == want)
    if btn is None:
        return (f"Opened {contact} but couldn't find the {want} button - the chat "
                f"may not have opened. Tell the user; don't claim it called.")
    try:
        btn.iface_invoke.Invoke()   # Invoke, not click_input: this is web content
    except Exception:
        try:
            btn.click_input()
        except Exception as e:
            return f"Found the {want} button but couldn't press it: {e}"
    time.sleep(1.0)
    return f"Started a {kind} call to {contact}. Tell the user it's ringing."
