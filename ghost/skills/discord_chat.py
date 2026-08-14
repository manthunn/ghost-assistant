"""Read and send Discord from the desktop app.

Named discord_chat rather than discord so it can never shadow the `discord`
package, the same reason calendar_feed isn't called calendar.

Discord is Electron, so the usual rule applies: the window reports 7 controls
until it has focus and 1088 after. _activate first or everything looks empty.

At ~1100 elements a full walk costs about 1.6 s, so unlike WhatsApp (42,000)
there's no need for a scoped breadth-first search - reading the whole tree is
affordable.

NOT a self-bot. Discord's terms prohibit automating a user account through
their API, so nothing here touches a token or an HTTP endpoint. This drives the
UI the way a screen reader does, and sending is a keystroke the user has
approved out loud. Sends are gated behind confirm=True for the same reason as
WhatsApp: a message in a shared server is public and instant.

Message rows read as:
    <Author><Server Tag: X?><DD/MM/YYYY H:MM AM><Weekday, D Month YYYY H:MM AM><text>
with the timestamp repeated in short then long form. Consecutive messages from
one author omit the name, so the author carries forward.
"""
import re
import time

from . import register
from .ui_automation import _find_window, _activate

WINDOW = "Discord"

# The long-form timestamp is the reliable anchor: everything after the last one
# in a row is the message body.
_LONG_STAMP = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"\d{1,2}\s+\w+\s+\d{4}\s+\d{1,2}:\d{2}\s*[AaPp][Mm]")
# Discord writes the short timestamp several ways depending on age and whether
# the message continues the previous author: "11/08/2026 11:57 AM",
# "Yesterday at 8:19 PM", "Today at 9:02 AM", or a bare "12:00 PM". All of them
# have to come off the head, or the leftover becomes the author's name.
_SHORT_STAMP = re.compile(
    r"(?:\d{1,2}/\d{1,2}/\d{4}\s*)?"
    r"(?:(?:Yesterday|Today)\s*)?(?:at\s*)?"
    r"\d{1,2}:\d{2}\s*[AaPp][Mm]", re.I)
_LEFTOVER_DATEWORDS = re.compile(r"\b(?:Yesterday|Today|at)\b\s*$", re.I)
_SERVER_TAG = re.compile(r"Server Tag:\s*\S+")
# Chrome Discord appends to the accessible name of a row.
_REACTION_JUNK = re.compile(
    r"(Click to react.*|Add Reaction.*|Add to Favorites.*|Jump To Reply.*"
    r"|:[a-z0-9_+-]+:)", re.I)


def _clean(s):
    return " ".join((s or "").split())


def _walk(win):
    """(control_type, name, element) tolerating the tree mutating mid-read."""
    out = []
    try:
        kids = win.descendants()
    except Exception:
        return out
    for c in kids:
        try:
            out.append((c.element_info.control_type, _clean(c.window_text()), c))
        except Exception:
            continue
    return out


def _window():
    win = _find_window(WINDOW)
    if win is None:
        return None
    return _activate(win, settle=2.0)


def _parse_message(text, last_author):
    """(author, body) - author is '' only if it can't be inferred."""
    text = _clean(text)
    stamps = list(_LONG_STAMP.finditer(text))
    if not stamps:
        return last_author, _clean(_REACTION_JUNK.sub("", text))

    body = _clean(_REACTION_JUNK.sub("", text[stamps[-1].end():]))
    head = text[:stamps[0].start()]

    # A reply row embeds the quoted message and ITS author ahead of the real
    # sender, with no delimiter between them - "@Min Hao love 5DS ishan" is the
    # person replied to, their message, then the actual author. The body is
    # still correct, but the name can't be recovered reliably. Say so instead of
    # attributing the message to a name stitched out of two people.
    if head.lstrip().startswith("@"):
        return "someone (in a reply, name unclear)", body

    head = _SERVER_TAG.sub("", head)
    head = _SHORT_STAMP.sub("", head)
    head = _LEFTOVER_DATEWORDS.sub("", _clean(head))
    author = _clean(head)
    # An empty head means this row continues the previous author's run, which
    # is how Discord renders consecutive messages from one person.
    return (author or last_author), body


@register({"name": "read_discord",
    "description": "Read recent messages from the Discord channel currently open on "
                    "screen. Use for 'what's happening in Discord', 'what did they "
                    "say in the channel', 'catch me up on Discord'. Reads whichever "
                    "channel is open - use check_discord first to see where the "
                    "unread activity is.",
    "parameters": {"type": "object", "properties": {
        "count": {"type": "integer", "description": "how many recent messages, default 15"}},
        "required": []}})
def read_discord(count: int = 15):
    win = _window()
    if win is None:
        return "Discord doesn't appear to be running."
    rows = _walk(win)
    lists = [(n, e) for t, n, e in rows
             if t == "List" and n.lower().startswith("messages in")]
    if not lists:
        return ("Discord is open but no message list was found - it may be showing "
                "a server home or settings rather than a channel.")

    name, lst = lists[0]
    channel = name[len("Messages in"):].strip() or "this channel"
    try:
        kids = lst.children()
    except Exception as e:
        return f"Couldn't read the messages in {channel}: {e}"

    out, author = [], ""
    for c in kids:
        try:
            if c.element_info.control_type != "ListItem":
                continue
            raw = c.window_text()
        except Exception:
            continue
        author, body = _parse_message(raw, author)
        if body:
            out.append((author or "someone", body))

    if not out:
        return f"No readable messages in #{channel}."
    out = out[-max(1, min(int(count or 15), 40)):]
    lines = [f"- {a}: {b[:300]}" for a, b in out]
    return (f"Last {len(lines)} messages in #{channel}:\n" + "\n".join(lines)
            + "\n\nSummarise the gist and who said what; don't read every line "
              "verbatim.")


@register({"name": "check_discord",
    "description": "See where there's unread Discord activity: which servers have "
                    "mentions or unread messages, and which channels are unread in "
                    "the server currently open. Use for 'anything on Discord', 'do I "
                    "have Discord mentions', 'what's unread'.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def check_discord():
    win = _window()
    if win is None:
        return "Discord doesn't appear to be running."
    rows = _walk(win)

    servers, channels = [], []
    for t, n, _e in rows:
        low = n.lower()
        if t == "TreeItem" and ("mention" in low or "unread" in low):
            servers.append(n)
        elif t in ("Hyperlink", "Button") and "unread" in low and "channel)" in low:
            # 'unread, ai-education (text channel), Private Channel (locked)'
            m = re.search(r"([\w\- ]+)\s*\((text|voice|forum) channel\)", n, re.I)
            if m:
                channels.append(f"#{m.group(1).strip()} ({m.group(2)})")

    title = _clean(win.window_text())
    here = title.split("|")[-1].replace("- Discord", "").strip() if "|" in title else ""

    parts = []
    if servers:
        parts.append("Servers with activity:\n"
                     + "\n".join(f"- {s}" for s in servers))
    if channels:
        seen, uniq = set(), []
        for c in channels:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        parts.append(f"Unread channels in {here or 'the current server'}:\n"
                     + "\n".join(f"- {c}" for c in uniq))
    if not parts:
        return "Nothing unread on Discord in the server that's currently open."
    return ("\n\n".join(parts)
            + "\n\nOnly the open server's channels are visible - say so rather than "
              "implying this covers every server.")


@register({"name": "open_discord_channel",
    "description": "Switch Discord to a named text channel, so it can then be read. "
                    "Use when the user names a channel ('what's in ai-education', "
                    "'check general'), or after check_discord shows unread activity "
                    "somewhere. Only reaches channels in the server currently open.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string",
                  "description": "channel name, with or without the leading #"}},
        "required": ["name"]}})
def open_discord_channel(name: str):
    want = _clean(name).lstrip("#").lower()
    if not want:
        return "Which channel?"
    win = _window()
    if win is None:
        return "Discord doesn't appear to be running."

    best = None
    for t, n, e in _walk(win):
        if t not in ("Hyperlink", "Button"):
            continue
        m = re.search(r"([\w\-' ]+?)\s*\((text|forum) channel\)", n, re.I)
        if not m:
            continue
        label = m.group(1).strip().lower()
        # Prefer an exact name; fall back to a containment match.
        if label == want:
            best = (label, e)
            break
        if want in label and best is None:
            best = (label, e)

    if best is None:
        return (f"No text channel matching '{name}' in the server that's open. "
                f"Only the current server's channels are reachable.")
    label, el = best
    try:
        el.iface_invoke.Invoke()   # Invoke, not click_input: Electron web content
    except Exception:
        try:
            el.click_input()
        except Exception as e:
            return f"Found #{label} but couldn't open it: {e}"
    time.sleep(1.5)
    return (f"Opened #{label}. Call read_discord to read it - don't guess at the "
            f"contents.")


@register({"name": "send_discord",
    "description": "Send a message to the Discord channel currently open. THIS POSTS "
                    "IMMEDIATELY, usually to a shared server where others see it, and "
                    "cannot be undone. Read the channel name and the exact message "
                    "back to the user and get a clear spoken yes, then call again "
                    "with confirm=true. Never pass confirm=true on your own "
                    "initiative.",
    "parameters": {"type": "object", "properties": {
        "message": {"type": "string", "description": "exact text to post"},
        "confirm": {"type": "boolean",
                     "description": "true only after the user heard the channel and "
                                    "message and said yes"}},
        "required": ["message"]}})
def send_discord(message: str, confirm: bool = False):
    message = (message or "").strip()
    if not message:
        return "Nothing to send."

    win = _window()
    if win is None:
        return "Discord doesn't appear to be running."
    title = _clean(win.window_text())
    channel = title.split("|")[0].strip() if "|" in title else "the open channel"

    if not confirm:
        return (f"NOT SENT - needs confirmation. Read this back and ask if it should "
                f"go: to {channel}, \"{message}\". If they say yes, call send_discord "
                f"again with confirm=true.")

    rows = _walk(win)
    box = next((e for t, n, e in rows if t == "Edit"), None)
    if box is None:
        return ("Couldn't find Discord's message box - the channel may not be open, "
                "or it may be read-only. Don't claim it sent.")
    try:
        box.set_focus()
        time.sleep(0.3)
        from pywinauto.keyboard import send_keys
        send_keys(message.replace("\n", " "), with_spaces=True, pause=0.01)
        time.sleep(0.3)
        send_keys("{ENTER}")
        time.sleep(0.6)
    except Exception as e:
        return f"Couldn't send to {channel}: {e}"
    return f"Posted to {channel}: \"{message}\"."
