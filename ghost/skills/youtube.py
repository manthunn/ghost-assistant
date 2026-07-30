"""Watch YouTube channels for new uploads.

Uses YouTube's public per-channel Atom feeds
(youtube.com/feeds/videos.xml?channel_id=...) rather than the Data API: no API
key, no quota, no Cloud project. The tradeoff is the feed only carries the ~15
most recent uploads, which is plenty for "anything new today".
"""
import json
import pathlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import requests
from . import register

CHANNELS_FILE = pathlib.Path(__file__).resolve().parent.parent / "youtube_channels.json"
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
UA = {"User-Agent": "Mozilla/5.0"}

def _load():
    if CHANNELS_FILE.exists():
        try:
            return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []

def _save(chans):
    CHANNELS_FILE.write_text(json.dumps(chans, indent=2), encoding="utf-8")

def _resolve(ref):
    """Accept a channel id, @handle, or any youtube URL and return (channel_id, name)."""
    ref = (ref or "").strip()
    if re.fullmatch(r"UC[\w-]{22}", ref):
        return ref, None
    if ref.startswith("http"):
        url = ref
    elif ref.startswith("@"):
        url = f"https://www.youtube.com/{ref}"
    else:
        url = f"https://www.youtube.com/@{ref.lstrip('@')}"
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    # The canonical link is the page's OWN channel. Scanning for the first
    # "channelId" in the HTML instead picks up recommended/embedded channels -
    # that made "Sidemen" resolve to MoreSidemen and "@mkbhd" to The Studio.
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
                  r.text)
    if not m:
        # og:url carries the same thing on some layouts
        m = re.search(r'<meta property="og:url" content="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
                      r.text)
    if not m:
        return None, None
    name = None
    nm = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
    if nm:
        name = nm.group(1)
    return m.group(1), name

def _entries(channel_id):
    r = requests.get(FEED.format(channel_id), headers=UA, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    channel = (root.findtext(f"{ATOM}title") or "").strip()
    out = []
    for e in root.findall(f"{ATOM}entry"):
        pub = e.findtext(f"{ATOM}published")
        when = None
        if pub:
            try:
                when = datetime.fromisoformat(pub)
            except ValueError:
                pass
        group = e.find(f"{MEDIA}group")
        views = None
        if group is not None:
            stat = group.find(f"{MEDIA}community/{MEDIA}statistics")
            if stat is not None:
                views = stat.get("views")
        out.append({
            "title": (e.findtext(f"{ATOM}title") or "").strip(),
            "url": (e.find(f"{ATOM}link").get("href") if e.find(f"{ATOM}link") is not None else ""),
            "published": when,
            "views": views,
        })
    return channel, out

@register({"name": "watch_youtube_channel",
    "description": "Add a YouTube channel to the watch list so Ghost reports its new "
                    "uploads. Accepts a channel name/handle (e.g. 'Sidemen', "
                    "'@mkbhd'), a channel URL, or a raw channel ID.",
    "parameters": {"type": "object", "properties": {
        "channel": {"type": "string", "description": "channel handle, name, URL or ID"}},
        "required": ["channel"]}})
def watch_youtube_channel(channel: str):
    try:
        cid, name = _resolve(channel)
    except Exception as e:
        return f"Couldn't look up '{channel}': {e}"
    if not cid:
        return (f"Couldn't find a YouTube channel for '{channel}'. Try the full channel "
                "URL or its @handle.")
    chans = _load()
    already = next((c for c in chans if c["id"] == cid), None)
    if already:
        return f"Already watching {already['name']}."
    try:
        real_name, _ = _entries(cid)
    except Exception:
        real_name = name or channel
    chans.append({"id": cid, "name": real_name or name or channel})
    _save(chans)
    return f"Now watching {real_name or name or channel} for new uploads."

@register({"name": "list_watched_channels",
    "description": "List the YouTube channels Ghost is watching for new uploads.",
    "parameters": {"type": "object", "properties": {}, "required": []}})
def list_watched_channels():
    chans = _load()
    if not chans:
        return "Not watching any YouTube channels yet."
    return "Watching: " + ", ".join(c["name"] for c in chans)

@register({"name": "unwatch_youtube_channel",
    "description": "Stop watching a YouTube channel, matched by name.",
    "parameters": {"type": "object", "properties": {
        "channel": {"type": "string"}}, "required": ["channel"]}})
def unwatch_youtube_channel(channel: str):
    q = (channel or "").lower().strip()
    chans = _load()
    keep = [c for c in chans if q not in c["name"].lower()]
    if len(keep) == len(chans):
        return f"Not watching anything matching '{channel}'."
    _save(keep)
    return f"Stopped watching {len(chans) - len(keep)} channel(s)."

@register({"name": "check_youtube_uploads",
    "description": "Check watched YouTube channels for uploads in the last N hours. "
                    "Use for 'any new videos', 'did X upload today'.",
    "parameters": {"type": "object", "properties": {
        "hours": {"type": "integer", "description": "how far back to look, default 24"},
        "channel": {"type": "string",
                     "description": "optional: only this channel (name, handle or URL)"}},
        "required": []}})
def check_youtube_uploads(hours: int = 24, channel: str = ""):
    hours = max(1, min(int(hours or 24), 24 * 14))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    if channel:
        cid, _ = _resolve(channel)
        if not cid:
            return f"Couldn't find a channel for '{channel}'."
        targets = [{"id": cid, "name": channel}]
    else:
        targets = _load()
        if not targets:
            return ("No channels on the watch list. Ask to watch one first, e.g. "
                    "'watch the Sidemen channel'.")

    lines = []
    for c in targets:
        try:
            name, entries = _entries(c["id"])
        except Exception as e:
            lines.append(f"{c['name']}: couldn't check ({e})")
            continue
        fresh = [e for e in entries if e["published"] and e["published"] >= cutoff]
        if not fresh:
            lines.append(f"{name or c['name']}: nothing new in the last {hours}h.")
            continue
        for e in fresh[:5]:
            age = datetime.now(timezone.utc) - e["published"]
            hrs = int(age.total_seconds() // 3600)
            when = f"{hrs}h ago" if hrs < 48 else e["published"].strftime("%d %b")
            views = f", {e['views']} views" if e["views"] else ""
            lines.append(f"{name or c['name']}: \"{e['title']}\" ({when}{views})")
    return "\n".join(lines)

def briefing_section(hours=24):
    """Compact block for the daily briefing; empty string if nothing is watched."""
    if not _load():
        return ""
    return check_youtube_uploads(hours)
