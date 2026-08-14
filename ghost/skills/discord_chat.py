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
# Alphabetic only, NOT \S+. There is no space between the tag and the timestamp
# that follows it ("Server Tag: MDN12:46 PM"), so a greedy \S+ swallows the time
# and leaves "PM" behind as the author's surname.
_SERVER_TAG = re.compile(r"Server Tag:\s*[A-Za-z]+")
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


def _current_target(win):
    """Human description of whatever is open, for the send confirmation.

    Titles differ by kind: '#technical-management | Monash DeepNeuron - Discord'
    for a channel, '@Aaron Elias - Discord' for a DM. Getting this right
    matters - the user has to hear WHO a message is about to go to, and "the
    open channel" is not good enough for a DM.
    """
    title = _clean(win.window_text()).replace("- Discord", "").strip()
    if title.startswith("@"):
        return f"the DM with {title.lstrip('@').strip()}"
    if "|" in title:
        chan, server = [p.strip() for p in title.split("|", 1)]
        return f"{chan} in {server}"
    return title or "the open conversation"


def _parse_message(text, last_author):
    """(author, body) - author is '' only if it can't be inferred."""
    text = _clean(text)
    stamps = list(_LONG_STAMP.finditer(text))
    if not stamps:
        return last_author, _clean(_REACTION_JUNK.sub("", text))

    # Body starts after the FIRST long stamp, not the last: an edited message
    # carries a second stamp AFTER its text ("...gotcha (edited)Friday, 14
    # August 2026 12:47 PM"), so anchoring on the last one threw the message
    # away and kept the reaction chrome.
    body = text[stamps[0].end():]
    body = _LONG_STAMP.sub(" ", body)
    body = _clean(_REACTION_JUNK.sub("", body))
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
    where = name[len("Messages in"):].strip() or "this channel"
    # A DM is a person, not a channel - "#Aaron Elias" reads as nonsense.
    is_dm = _clean(win.window_text()).startswith("@")
    channel = where if is_dm else f"#{where}"
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
        return f"No readable messages in {channel}."
    out = out[-max(1, min(int(count or 15), 40)):]
    lines = [f"- {a}: {b[:300]}" for a, b in out]
    return (f"Last {len(lines)} messages in {channel}:\n" + "\n".join(lines)
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
    dms = _dm_entries(rows)

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
    if dms:
        parts.append("Recent DM conversations:\n"
                     + "\n".join(f"- {n}" + (" (group)" if k == "group" else "")
                                 + (f": {p}" if p else "")
                                 for n, k, _s, p, _e in dms[:6]))
    if not parts:
        return "Nothing unread on Discord in the server that's currently open."
    return ("\n\n".join(parts)
            + "\n\nOnly the open server's channels are visible, and the DM previews "
              "are the latest message in each thread rather than proof it's unread. "
              "Say so rather than implying this covers everything.")


def _dm_entries(rows):
    """[(display_name, kind, status, preview, element)] from the DM sidebar.

    The ListItem's own text is a concatenation of the name (twice), the server
    tag and the preview (also twice), with no delimiters - unusable directly.
    The reliable identifier is a child whose name ends in "(direct message)" or
    "(group message)", so that is what's matched on.
    """
    out = []
    lists = [e for t, n, e in rows
             if t == "List" and _clean(n).lower() == "direct messages"]
    if not lists:
        return out
    try:
        kids = lists[0].children()
    except Exception:
        return out

    for item in kids:
        try:
            if item.element_info.control_type != "ListItem":
                continue
            raw = _clean(item.window_text())
            subs = []
            for k in item.descendants():
                try:
                    subs.append(_clean(k.window_text()))
                except Exception:
                    continue
        except Exception:
            continue

        ident = next((s for s in subs
                      if "(direct message)" in s or "(group message)" in s), None)
        if not ident:
            continue
        kind = "group" if "(group message)" in ident else "dm"
        name = ident.split("(")[0].strip().rstrip(",").strip()
        tail = ident.split(")", 1)[1].lstrip(", ").strip() if ")" in ident else ""

        # Preview: strip the duplicated name and server tag off the blob, then
        # collapse the repetition Discord leaves behind.
        preview = raw
        for _ in range(2):
            if preview.startswith(name):
                preview = preview[len(name):].lstrip()
        preview = _SERVER_TAG.sub("", preview).strip()
        half = len(preview) // 2
        if half > 6 and preview[:half].strip() and preview.startswith(preview[:half].strip()[:20]):
            first = preview[:half].strip()
            if preview.replace(first, "", 1).strip().startswith(first[:15]):
                preview = first
        out.append((name, kind, tail, _clean(preview)[:120], item))
    return out


@register({"name": "list_discord_dms",
    "description": "List the user's Discord direct-message conversations, with the "
                    "latest message preview where visible. Use for 'who's DM'd me', "
                    "'check my Discord DMs', 'any direct messages'. To then read one, "
                    "call open_discord_dm and then read_discord.",
    "parameters": {"type": "object", "properties": {
        "count": {"type": "integer", "description": "how many conversations, default 10"}},
        "required": []}})
def list_discord_dms(count: int = 10):
    win = _window()
    if win is None:
        return "Discord doesn't appear to be running."
    entries = _dm_entries(_walk(win))
    if not entries:
        return ("Couldn't read the DM list - Discord may be showing a server "
                "instead. Ask the user to click the Discord home icon, or try again.")
    entries = entries[:max(1, min(int(count or 10), 25))]
    lines = []
    for name, kind, status, preview, _el in entries:
        tag = " (group)" if kind == "group" else ""
        bit = f"- {name}{tag}"
        if preview:
            bit += f": {preview}"
        lines.append(bit)
    return (f"{len(lines)} Discord conversations:\n" + "\n".join(lines)
            + "\n\nThese previews are the last message in each thread, not "
              "necessarily unread. Summarise; don't read every line out.")


@register({"name": "open_discord_dm",
    "description": "Open a direct-message conversation with a named person (or a "
                    "group DM) in Discord, so it can then be read or replied to. Use "
                    "before read_discord or send_discord when the user names someone.",
    "parameters": {"type": "object", "properties": {
        "person": {"type": "string", "description": "the person's display name"}},
        "required": ["person"]}})
def open_discord_dm(person: str):
    want = _clean(person).lower()
    if not want:
        return "Who should I open?"
    win = _window()
    if win is None:
        return "Discord doesn't appear to be running."

    entries = _dm_entries(_walk(win))
    if not entries:
        return "Couldn't read the DM list."
    match = next((e for e in entries if e[0].lower() == want), None)
    if match is None:
        match = next((e for e in entries if want in e[0].lower()), None)
    if match is None:
        have = ", ".join(e[0] for e in entries[:10])
        return (f"No open DM with '{person}'. Conversations visible: {have}. "
                f"Starting a brand new DM isn't supported - say so.")

    name, _kind, _status, _preview, el = match
    # The ListItem itself exposes no Invoke pattern - only its Hyperlink child
    # does. Invoking the container silently does nothing, and click_input()
    # doesn't raise either, so without this the tool cheerfully reported
    # opening a conversation it had not opened.
    target = el
    try:
        for k in el.descendants():
            if k.element_info.control_type == "Hyperlink" and \
                    "message)" in _clean(k.window_text()):
                target = k
                break
    except Exception:
        pass

    before = _clean(win.window_text())
    opened = False
    for attempt in (target.iface_invoke.Invoke, target.click_input):
        try:
            attempt()
            time.sleep(1.5)
            if _clean(win.window_text()) != before:
                opened = True
                break
        except Exception:
            continue

    # Verify by the window title actually changing rather than by the absence
    # of an exception.
    if not opened:
        return (f"Couldn't open the DM with {name} - the click didn't take. "
                f"Tell the user to open it themselves; do NOT read whatever is "
                f"currently on screen and present it as {name}'s messages.")
    return f"Opened the DM with {name}. Call read_discord to read it."


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
    channel = _current_target(win)

    if not confirm:
        return (f"NOT SENT - needs confirmation. Read this back and ask if it should "
                f"go: to {channel}, \"{message}\". Name the recipient explicitly - a "
                f"DM to the wrong person can't be taken back. If they say yes, call "
                f"send_discord again with confirm=true.")

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
