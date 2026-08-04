"""Financial Times headlines, filtered down to what actually matters.

Uses FT's public RSS feeds: headline, standfirst, timestamp and link, no API
key and no login. Article bodies are paywalled and deliberately NOT fetched -
Manny has a subscription, so the right move is to hand him the link and let him
read it in his own session, not to scrape text Ghost isn't entitled to.

The point of this skill is selection, not retrieval. Pulling 100 headlines and
reading them out is useless. The tool returns the candidates plus explicit
instructions to be decisive: some days three stories matter, some days one,
some days none, and saying "nothing major today" is a valid and useful answer.

Kept out of the daily briefing on purpose - Manny asked for it as its own
thing, so the briefing stays about his day and this stays about the world.
"""
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

from . import register

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_TTL = timedelta(minutes=15)
_cache = {}

# 'home' is FT's own front-page selection - the closest thing to an editorial
# judgement of what matters today, so it leads. The sections widen the net.
FEEDS = {
    "home": "https://www.ft.com/rss/home",
    "world": "https://www.ft.com/world?format=rss",
    "companies": "https://www.ft.com/companies?format=rss",
    "markets": "https://www.ft.com/markets?format=rss",
    "technology": "https://www.ft.com/technology?format=rss",
}
DEFAULT_SECTIONS = ("home", "world", "companies", "markets", "technology")


def _fetch(section):
    url = FEEDS[section]
    hit = _cache.get(section)
    if hit and datetime.now() - hit[0] < CACHE_TTL:
        return hit[1]
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    _cache[section] = (datetime.now(), r.content)
    return r.content


def _items(section):
    out = []
    try:
        root = ET.fromstring(_fetch(section))
    except Exception:
        return out
    for it in root.findall(".//item"):
        title = " ".join((it.findtext("title") or "").split())
        if not title:
            continue
        when = None
        raw = it.findtext("pubDate")
        if raw:
            try:
                when = parsedate_to_datetime(raw)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except Exception:
                when = None
        out.append({
            "title": title,
            "summary": " ".join((it.findtext("description") or "").split())[:280],
            "when": when,
            "link": (it.findtext("link") or "").strip(),
            "section": section,
        })
    return out


def _age(when, now):
    if when is None:
        return "time unknown"
    mins = int((now - when).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 0)}m ago"
    if mins < 60 * 24:
        return f"{mins // 60}h ago"
    return f"{mins // (60 * 24)}d ago"


def collect(hours=24, sections=None):
    """(items, errors) - deduped by headline, newest first."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, min(int(hours or 24), 168)))
    seen, out, errors = set(), [], []
    for s in (sections or DEFAULT_SECTIONS):
        if s not in FEEDS:
            continue
        got = _items(s)
        if not got:
            errors.append(s)
            continue
        for it in got:
            key = it["title"].lower()
            if key in seen:
                continue
            if it["when"] is not None and it["when"] < cutoff:
                continue
            seen.add(key)
            out.append(it)
    out.sort(key=lambda i: i["when"] or datetime.min.replace(tzinfo=timezone.utc),
             reverse=True)
    return out, errors


# The whole value of this skill is in the instruction below, not the feed.
_GUIDANCE = (
    "\nHOW TO REPORT THIS - read carefully:\n"
    "- These are CANDIDATES, not a list to read out. Pick only the stories that "
    "genuinely matter and ignore the rest.\n"
    "- The number varies by day. Some days three stories are worth hearing, some "
    "days one, some days five. Do NOT aim for a fixed count and do not pad.\n"
    "- If nothing significant happened, say exactly that. 'Nothing major in the FT "
    "today' is a good answer, not a failure.\n"
    "- Weight real-world consequence: markets, policy, conflict, major company and "
    "technology moves. Skip routine market chatter, columns, lifestyle and "
    "personality pieces unless they carry real news.\n"
    "- Give each pick one or two spoken sentences of what happened and why it "
    "matters. Don't just read the headline back.\n"
    "- Only what's here is available. The full articles are paywalled, so do not "
    "invent detail beyond the headline and standfirst. Offer to open a link if he "
    "wants to read one - he has an FT subscription."
)


@register({"name": "ft_news",
    "description": "Get the day's Financial Times headlines and pick out the ones that "
                    "actually matter. Use when the user asks what's happening in the "
                    "news, what's going on in the world/markets, or asks for the FT. "
                    "Be selective with the result - report only genuinely significant "
                    "stories, however many that is, and say so if there are none.",
    "parameters": {"type": "object", "properties": {
        "hours": {"type": "integer",
                   "description": "how far back to look, default 24"},
        "topic": {"type": "string",
                   "description": "optional filter, e.g. 'markets', 'technology', "
                                  "'india', 'AI'. Matches section names and headline "
                                  "text. Omit for everything."}},
        "required": []}})
def ft_news(hours: int = 24, topic: str = ""):
    try:
        items, errors = collect(hours)
    except Exception as e:
        return f"Couldn't reach the FT feeds: {e}"
    if not items:
        return ("No FT stories came back" +
                (f" (failed sections: {', '.join(errors)})" if errors else "") +
                ". Tell the user plainly rather than guessing at the news.")

    q = " ".join((topic or "").split()).lower()
    if q:
        picked = [i for i in items
                  if q in i["title"].lower() or q in i["summary"].lower()
                  or q == i["section"]]
        if not picked:
            return (f"Nothing in the FT's last {hours}h matches '{topic}'. Say so - "
                    f"don't substitute unrelated stories or invent coverage.")
    else:
        picked = items

    now = datetime.now(timezone.utc)
    lines = [f"FT headlines, last {hours}h"
             + (f", filtered on '{topic}'" if q else "")
             + f" ({len(picked)} candidates):", ""]
    for i in picked[:40]:
        lines.append(f"- [{i['section']}, {_age(i['when'], now)}] {i['title']}")
        if i["summary"]:
            lines.append(f"    {i['summary']}")
        if i["link"]:
            lines.append(f"    {i['link']}")
    if errors:
        lines.append(f"\n(couldn't load sections: {', '.join(errors)})")
    return "\n".join(lines) + "\n" + _GUIDANCE
